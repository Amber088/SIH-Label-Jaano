import 'dart:typed_data';

import 'compliance_report.dart';
import 'saved_scan.dart';

/// One inspection as the app holds it: the photos captured, the report the
/// backend returned, and when. Held by [ScanStore], which the dashboard and the
/// officer queue are aggregated from.
///
/// A record can come from either of two places, and the difference matters:
///
/// * a scan just performed on this device — it has [thumbnails], and a
///   [serverId] only if the user was signed in and the server filed it;
/// * a row hydrated from the server's history via [ScanRecord.fromSaved] — it has
///   a [serverId] but no photos, because the API never stores the image.
///
/// The server is the source of truth for anything with a [serverId]. Records
/// without one are local-only: an anonymous scan, or one taken with `save=false`,
/// and they are gone when the app closes. That is honest rather than unfortunate —
/// pretending otherwise would mean showing an officer a history the server cannot
/// corroborate.
class ScanRecord {
  ScanRecord({
    required this.id,
    required this.capturedAt,
    required this.report,
    this.thumbnails = const [],
    this.note,
    this.serverMock = false,
    this.serverId,
    this.productName,
    this.location,
    this.ownerId,
  });

  final String id;
  final DateTime capturedAt;
  final ComplianceReport report;

  /// Front (and optionally back) panel image bytes, kept only for this session
  /// so the report and queue can show what was scanned. Empty for a record
  /// hydrated from the server, which has no photo to hand back.
  final List<Uint8List> thumbnails;

  /// Officer's note (e.g. shop name, remarks) — free text, stored with the scan.
  final String? note;

  /// True if this scan was produced by the backend's offline mock pipeline
  /// rather than live OCR + Gemini. Surfaced so a demo verdict is never
  /// mistaken for a live model read.
  final bool serverMock;

  /// The id the server filed this under, if it did. Null means local-only: no
  /// account, no database, or `save=false`. Everything that talks to the server
  /// about a specific inspection — export, share, delete — keys off this, so a
  /// null here is precisely why those actions are unavailable on such a record.
  final String? serverId;

  /// What the inspection is of. Sent to the server so a history row is
  /// identifiable by something other than a UUID.
  final String? productName;

  /// Place of inspection.
  final String? location;

  /// The account that filed it. Meaningful only in an officer's queue, which
  /// spans other inspectors' work; null on a local-only record.
  final String? ownerId;

  /// Hydrate a server history row.
  ///
  /// [report] is required rather than read from [saved] because the list endpoint
  /// deliberately omits the report body — so the caller has to say which one it
  /// has, and a summary row is turned into a record only once its report has
  /// actually been fetched.
  factory ScanRecord.fromSaved(SavedScan saved, {ComplianceReport? report}) {
    final body = report ?? saved.report;
    if (body == null) {
      throw ArgumentError(
        'ScanRecord.fromSaved needs a report: fetch GET /scans/${saved.id} '
        'first, or pass one in. The list endpoint omits the report body.',
      );
    }
    return ScanRecord(
      id: saved.id,
      capturedAt: saved.capturedAt ?? DateTime.now(),
      report: body,
      note: saved.note,
      serverMock: saved.mock,
      serverId: saved.id,
      productName: saved.productName,
      location: saved.location,
      ownerId: saved.userId,
    );
  }

  ScanRecord copyWith({
    String? serverId,
    String? productName,
    String? location,
    String? note,
    String? ownerId,
    List<Uint8List>? thumbnails,
  }) =>
      ScanRecord(
        id: id,
        capturedAt: capturedAt,
        report: report,
        thumbnails: thumbnails ?? this.thumbnails,
        note: note ?? this.note,
        serverMock: serverMock,
        serverId: serverId ?? this.serverId,
        productName: productName ?? this.productName,
        location: location ?? this.location,
        ownerId: ownerId ?? this.ownerId,
      );

  Verdict get verdict => report.verdict;
  double get score => report.score;

  /// Present this local scan in the same shape as a server history row.
  ///
  /// The queue and the dashboard consume one row type, [SavedScan], whether the
  /// user is signed in or not. Converting here rather than branching in every
  /// widget means anonymous mode is not a second, less-tested rendering path — it
  /// is the same path with [SavedScan.filed] false.
  SavedScan toSaved() => SavedScan(
        id: id,
        createdAt: capturedAt.toUtc().toIso8601String(),
        verdict: report.verdict,
        score: report.score,
        category: report.category,
        userId: ownerId,
        packsApplied: report.packsApplied,
        checksTotal: report.summary.checksTotal,
        violationsTotal: report.summary.violationsTotal,
        violationsBySeverity: {
          Severity.critical: report.summary.critical,
          Severity.major: report.summary.major,
          Severity.minor: report.summary.minor,
        },
        source: thumbnails.isEmpty ? 'json' : 'image',
        mock: serverMock,
        productName: productName,
        note: note,
        location: location,
        report: report,
        filed: isFiled,
      );

  /// True when the server has a copy, and therefore when export, share and
  /// server-side delete are possible.
  bool get isFiled => serverId != null && serverId!.isNotEmpty;

  /// Whether this record was hydrated from history rather than scanned here.
  bool get isRemote => isFiled && thumbnails.isEmpty;

  /// Heading for a list row or a report header.
  String get title {
    final name = (productName ?? '').trim();
    if (name.isNotEmpty) return name;
    return report.categoryLabel;
  }
}
