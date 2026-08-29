import 'package:flutter/foundation.dart';

import '../models/compliance_report.dart';
import '../models/saved_scan.dart';
import '../models/scan_record.dart';
import 'api_client.dart';
import 'session.dart';

/// The inspection history the app displays, from two sources at once.
///
/// **The server is the source of truth.** Once signed in, [sync] pulls the rows
/// the server holds — for an officer that is every inspection filed by anyone,
/// which no device-local store could ever produce. Local records exist for the
/// scans made on this device, and they matter for two reasons: they carry the
/// photos (the API never stores an image), and they are all an anonymous user
/// has.
///
/// The two are merged by server id, so a scan just taken by a signed-in user
/// appears once — from the local record until the next sync, from the server row
/// afterwards. The local copy is kept even then, because it is the only place the
/// thumbnails live.
///
/// Everything downstream consumes one row type, [SavedScan] (see [rows]). That is
/// why anonymous mode is not a separate, less-tested rendering path: it is the
/// same widgets over rows whose [SavedScan.filed] is false.
class ScanStore extends ChangeNotifier {
  ScanStore({
    required ApiClient api,
    required String Function() baseUrl,
    required Session session,
  })  : _api = api,
        _baseUrl = baseUrl,
        _session = session;

  final ApiClient _api;
  final String Function() _baseUrl;
  final Session _session;

  /// Scans performed on this device this session. Hold the photos.
  final List<ScanRecord> _local = [];

  /// Rows fetched from the server.
  final List<SavedScan> _remote = [];

  bool _syncing = false;
  String? _syncError;
  DateTime? _syncedAt;
  String _scope = 'own';
  int _serverTotal = 0;

  /// Server-computed aggregates, when signed in.
  ///
  /// Fetched rather than derived from [rows] because the two answer different
  /// questions. An officer's corpus is larger than one page, and `top_violations`
  /// is a corpus-wide GROUP BY — "which declaration do sellers breach most often"
  /// is not a question any page of rows can answer.
  ServerStats? _stats;

  /// One request pulls this many rows — the server's own ceiling on `limit`.
  ///
  /// Deliberately one page rather than a pagination state machine: the officer's
  /// queue is a working list, [serverTotal] states honestly how many rows exist
  /// behind it, and search narrows server-side. An infinite scroll here would be
  /// three more failure modes for no extra answer.
  static const int pageSize = 200;

  bool get syncing => _syncing;
  String? get syncError => _syncError;
  DateTime? get syncedAt => _syncedAt;

  /// 'own' or 'all' — which corpus the server actually searched, as reported by
  /// the server rather than inferred from the role.
  String get scope => _scope;
  bool get spansEveryone => _scope == 'all';

  /// How many rows the server holds behind the loaded page.
  int get serverTotal => _serverTotal;
  bool get hasUnloadedRows => _serverTotal > _remote.length;

  /// Server aggregates, or null when signed out — in which case the dashboard
  /// falls back to computing them over [rows], which is exactly right for a
  /// device-local session.
  ServerStats? get stats => _stats;

  /// Every row to display, most recent first.
  ///
  /// A local record whose server id already appears in [_remote] is dropped: the
  /// server's copy is authoritative, and showing both would double-count the scan
  /// in every aggregate on the dashboard.
  List<SavedScan> get rows {
    final remoteIds = _remote.map((r) => r.id).toSet();
    final merged = <SavedScan>[
      ..._remote,
      for (final r in _local)
        if (!(r.isFiled && remoteIds.contains(r.serverId))) r.toSaved(),
    ];
    merged.sort((a, b) {
      final at = a.capturedAt, bt = b.capturedAt;
      if (at == null || bt == null) return 0;
      return bt.compareTo(at);
    });
    return List.unmodifiable(merged);
  }

  bool get isEmpty => _remote.isEmpty && _local.isEmpty;
  int get total => rows.length;

  /// Rows in display order. Kept as a separate name because the dashboard's
  /// "recent activity" reads better than `rows.take(n)` at the call site.
  List<SavedScan> get recent => rows;

  /// Local scans not filed on the server — anonymous, `save=false`, or a storage
  /// failure. Surfaced so the queue can say so instead of silently offering
  /// export on a record the server has never heard of.
  List<SavedScan> get unfiled =>
      List.unmodifiable(rows.where((r) => !r.filed));

  // ----------------------------------------------------------------------- //
  // Local records
  // ----------------------------------------------------------------------- //
  void add(ScanRecord record) {
    _local.add(record);
    notifyListeners();
  }

  /// The local record behind a row, if this device took the scan. The only place
  /// the photos are, so the report screen asks here before drawing thumbnails.
  ScanRecord? localFor(String id) {
    for (final r in _local) {
      if (r.id == id || (r.serverId != null && r.serverId == id)) return r;
    }
    return null;
  }

  SavedScan? byId(String id) {
    for (final r in rows) {
      if (r.id == id) return r;
    }
    return null;
  }

  /// Forget everything held locally. Does **not** touch the server — deleting a
  /// filed inspection is [deleteScan], because wiping a screenful of records off
  /// a device is a very different act from destroying evidence on the server.
  void clearLocal() {
    _local.clear();
    notifyListeners();
  }

  /// Drop every row, local and fetched. Used when the session ends or the server
  /// address changes: the rows on screen belonged to a different account or a
  /// different server, and leaving them visible would be a small data leak with
  /// a very confusing UI.
  void reset() {
    _local.clear();
    _remote.clear();
    _stats = null;
    _syncError = null;
    _syncedAt = null;
    _scope = 'own';
    _serverTotal = 0;
    notifyListeners();
  }

  // ----------------------------------------------------------------------- //
  // Server
  // ----------------------------------------------------------------------- //

  /// Pull history from the server. No-op when not signed in.
  ///
  /// Never throws: this is called on tab entry and pull-to-refresh, where an
  /// exception would take out the screen. The failure lands in [syncError] and
  /// the previously loaded rows stay on screen — a stale list beats an empty one
  /// when someone is standing in a shop with bad signal.
  Future<void> sync({String? verdict, String? search}) async {
    final token = _session.token;
    if (token == null) return;
    if (_syncing) return;

    _syncing = true;
    _syncError = null;
    notifyListeners();
    try {
      final page = await _api.scans(
        baseUrl: _baseUrl(),
        token: token,
        verdict: verdict,
        search: search,
        limit: pageSize,
      );
      _remote
        ..clear()
        ..addAll(page.items);
      _scope = page.scope;
      _serverTotal = page.total;
      _syncedAt = DateTime.now();
    } on ApiException catch (e) {
      if (_session.handleFailure(e)) {
        // The session is over; the rows belonged to it.
        _remote.clear();
        _stats = null;
        _serverTotal = 0;
      }
      _syncError = e.message;
    } finally {
      _syncing = false;
      notifyListeners();
    }
    // Aggregates ride along with every refresh, deliberately. Pull-to-refresh on
    // any tab then brings the whole picture up to date, instead of leaving the
    // dashboard quoting numbers from before the row you just added or deleted.
    await _loadStats();
  }

  /// Refresh the server aggregates. Silent on failure: the dashboard falls back
  /// to computing over [rows], which is a slightly narrower answer rather than a
  /// wrong one, and an error banner for it would sit on top of the one [sync]
  /// already showed.
  Future<void> _loadStats() async {
    final token = _session.token;
    if (token == null) {
      if (_stats == null) return;
      _stats = null;
      notifyListeners();
      return;
    }
    try {
      _stats = await _api.stats(baseUrl: _baseUrl(), token: token);
      notifyListeners();
    } on ApiException catch (e) {
      _session.handleFailure(e);
    }
  }

  /// Fetch the full stored report for a row, for the report screen.
  ///
  /// The list endpoint omits report bodies, so opening an inspection filed on
  /// another device needs this. Returns null when there is nothing to fetch (not
  /// signed in, or the row was never filed) — the caller then falls back to the
  /// local record, which is the only copy in that case.
  Future<SavedScan?> loadDetail(String scanId) async {
    final token = _session.token;
    if (token == null) return null;
    try {
      final detail =
          await _api.scan(baseUrl: _baseUrl(), token: token, scanId: scanId);
      final at = _remote.indexWhere((r) => r.id == scanId);
      if (at >= 0) {
        _remote[at] = detail;
        notifyListeners();
      }
      return detail;
    } on ApiException catch (e) {
      _session.handleFailure(e);
      rethrow;
    }
  }

  /// A short-lived link that opens this report in a browser.
  Future<ReportLink> shareReport(String scanId, {int minutes = 15}) async {
    final token = _session.token;
    if (token == null) {
      throw ApiException('Sign in to share an inspection report.');
    }
    try {
      return await _api.shareReport(
          baseUrl: _baseUrl(), token: token, scanId: scanId, minutes: minutes);
    } on ApiException catch (e) {
      _session.handleFailure(e);
      rethrow;
    }
  }

  /// Delete an inspection.
  ///
  /// A filed row is deleted on the server first and dropped locally only if that
  /// succeeds — otherwise the app would show a record as gone while the server
  /// still held it, and the next refresh would resurrect it. An unfiled row is
  /// purely local, so there is nothing to ask.
  Future<void> deleteScan(String id) async {
    final row = byId(id);
    final token = _session.token;

    if (row != null && row.filed && token != null) {
      try {
        await _api.deleteScan(baseUrl: _baseUrl(), token: token, scanId: id);
      } on ApiException catch (e) {
        // A 404 means it is already gone — the officer's goal is met, so treat it
        // as success rather than making them tap delete on a phantom.
        if (!e.isMissing) {
          _session.handleFailure(e);
          rethrow;
        }
      }
    }
    _remote.removeWhere((r) => r.id == id);
    _local.removeWhere((r) => r.id == id || r.serverId == id);
    if (_serverTotal > 0) _serverTotal--;
    notifyListeners();
    // The corpus changed, so the dashboard's aggregates are now one row stale.
    if (row != null && row.filed && token != null) await _loadStats();
  }

  // ----------------------------------------------------------------------- //
  // Aggregates for the dashboard
  //
  // Computed over the loaded rows, which is exactly right for a consumer and for
  // anonymous use. An officer's corpus can be larger than one page, so the
  // dashboard prefers the server's own /stats when signed in and falls back to
  // these — see DashboardScreen.
  // ----------------------------------------------------------------------- //
  int countByVerdict(Verdict v) => rows.where((r) => r.verdict == v).length;

  Map<Verdict, int> get verdictCounts => {
        Verdict.compliant: countByVerdict(Verdict.compliant),
        Verdict.needsReview: countByVerdict(Verdict.needsReview),
        Verdict.nonCompliant: countByVerdict(Verdict.nonCompliant),
      };

  /// Inspections that actually evaluated a label. "No label detected" reads are
  /// mis-aimed photos, not inspections, so they are excluded from the score and
  /// rate aggregates below.
  List<SavedScan> get _scored => rows.where((r) => r.isScored).toList();

  double get averageScore {
    final scored = _scored;
    if (scored.isEmpty) return 0;
    return scored.fold<double>(0, (a, r) => a + r.score) / scored.length;
  }

  Map<Severity, int> get violationTotals {
    var critical = 0, major = 0, minor = 0;
    for (final r in rows) {
      critical += r.countOfSeverity(Severity.critical);
      major += r.countOfSeverity(Severity.major);
      minor += r.countOfSeverity(Severity.minor);
    }
    return {
      Severity.critical: critical,
      Severity.major: major,
      Severity.minor: minor,
    };
  }

  int get totalViolations => violationTotals.values.fold<int>(0, (a, b) => a + b);

  /// Share of scored inspections that came back fully compliant, as a percentage.
  double get complianceRate {
    final scored = _scored;
    if (scored.isEmpty) return 0;
    return 100.0 * countByVerdict(Verdict.compliant) / scored.length;
  }
}
