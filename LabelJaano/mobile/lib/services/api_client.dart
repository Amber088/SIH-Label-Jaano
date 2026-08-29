import 'dart:async';
import 'dart:convert';
import 'dart:io' show SocketException;
import 'dart:typed_data';

import 'package:http/http.dart' as http;

import '../models/account.dart';
import '../models/compliance_report.dart';
import '../models/saved_scan.dart';

/// One image to upload, with the filename the server will see.
class LabelImage {
  LabelImage(this.bytes, this.filename);
  final Uint8List bytes;
  final String filename;
}

/// A friendly, user-facing failure. Screens show [message] directly.
///
/// [statusCode] is what callers switch on, and one value gets special treatment:
/// 401 means the session is over, so [isAuthFailure] exists to let [Session] drop
/// the token rather than leave the app holding a credential the server has
/// already stopped honouring.
class ApiException implements Exception {
  ApiException(this.message, {this.statusCode});
  final String message;
  final int? statusCode;

  /// The session is gone: expired, revoked, or minted by a different server.
  bool get isAuthFailure => statusCode == 401;

  /// The account exists but is not allowed to do this. Distinct from 401 because
  /// signing in again will not help.
  bool get isForbidden => statusCode == 403;

  /// This server has no database, so accounts and history are switched off.
  /// Scanning is unaffected — the app should degrade, not error out.
  bool get isPersistenceUnavailable => statusCode == 503;

  /// The row is not there, or not visible to this caller. The API returns 404
  /// rather than 403 for another user's records on purpose, so these are the
  /// same case to the client.
  bool get isMissing => statusCode == 404;

  @override
  String toString() => message;
}

/// The result of a scan: the report, plus what the server did with it.
///
/// Returned instead of a bare [ComplianceReport] because "was this filed, and
/// under what id" is not part of the verdict but is exactly what the UI needs to
/// decide whether export and share are available.
class ScanOutcome {
  const ScanOutcome({
    required this.report,
    this.scanId,
    this.saved = false,
    this.extractionMock = false,
    this.extractionReason = '',
  });

  final ComplianceReport report;

  /// Server id, when the scan was filed. Null for an anonymous scan or `save=false`.
  final String? scanId;
  final bool saved;

  /// True when *any* extraction stage ran offline, so the values judged here are
  /// canned rather than read from the photo. The single most important flag in
  /// this class: without it a photo of a book can come back compliant and look
  /// like a finding.
  final bool extractionMock;

  /// Which stage fell back, and why. Shown verbatim.
  final String extractionReason;

  factory ScanOutcome.fromJson(Map<String, dynamic> json) {
    final extraction = (json['extraction'] as Map?)?.cast<String, dynamic>();
    return ScanOutcome(
      report: ComplianceReport.fromJson(json),
      scanId: json['scan_id'] as String?,
      saved: json['saved'] == true,
      extractionMock: extraction?['mock'] == true,
      extractionReason: (extraction?['reason'] ?? '').toString(),
    );
  }
}

/// Thin client over the Label Jaano FastAPI backend.
///
/// Two things are deliberately *not* this class's job.
///
/// It does not hold the base URL: that is passed per call, because Settings can
/// change the server address live and a cached copy would go stale.
///
/// It does not decide when a token is valid, only how to send one. Tokens arrive
/// as an optional [token] argument, and [Session] owns the question of which
/// token that is. Keeping authority out of the transport layer is why there is no
/// path here that can accidentally send a stale credential — there is nothing
/// here to go stale.
///
/// Every endpoint below works signed-in; the scanning ones also work with no
/// token at all, which is the consumer mode and the reason `token` is nullable
/// rather than required.
class ApiClient {
  ApiClient({http.Client? client}) : _client = client ?? http.Client();

  final http.Client _client;

  static const Duration _scanTimeout = Duration(seconds: 90);
  static const Duration _shortTimeout = Duration(seconds: 12);

  Uri _uri(String base, String path, [Map<String, String>? query]) {
    final uri = Uri.parse('${normaliseBase(base)}$path');
    if (query == null || query.isEmpty) return uri;
    return uri.replace(queryParameters: {...uri.queryParameters, ...query});
  }

  /// Tidy a user-typed server address: trim, drop a trailing slash, assume http.
  /// Public because the share sheet joins a server-supplied relative path to the
  /// same base and must normalise it identically.
  static String normaliseBase(String base) {
    var b = base.trim();
    while (b.endsWith('/')) {
      b = b.substring(0, b.length - 1);
    }
    if (!b.startsWith('http://') && !b.startsWith('https://')) b = 'http://$b';
    return b;
  }

  static Map<String, String> _auth(String? token) =>
      (token == null || token.isEmpty) ? const {} : {'Authorization': 'Bearer $token'};

  static Map<String, String> _jsonHeaders(String? token) => {
        'Content-Type': 'application/json',
        ..._auth(token),
      };

  // ----------------------------------------------------------------------- //
  // Accounts
  // ----------------------------------------------------------------------- //

  /// What this server will accept at sign-up, so the form can be drawn honestly.
  ///
  /// Never throws: a server that is down, ancient, or running without a database
  /// all mean the same thing to the sign-in screen — no accounts here — and
  /// making the caller handle three failures to learn one fact would only push
  /// this same fallback up a layer.
  Future<AuthConfig> authConfig(String baseUrl) async {
    try {
      final resp = await _guard(
        () => _client.get(_uri(baseUrl, '/auth/config')).timeout(_shortTimeout),
      );
      return AuthConfig.fromJson(_decode(resp));
    } on ApiException {
      return AuthConfig.unavailable;
    }
  }

  Future<AuthSession> register({
    required String baseUrl,
    required String email,
    required String password,
    String name = '',
    Role role = Role.consumer,
    String? officerCode,
  }) async {
    final body = <String, dynamic>{
      'email': email.trim(),
      'password': password,
      'name': name.trim(),
      'role': role.wire,
    };
    final code = (officerCode ?? '').trim();
    if (code.isNotEmpty) body['officer_code'] = code;

    final resp = await _guard(() => _client
        .post(_uri(baseUrl, '/auth/register'),
            headers: _jsonHeaders(null), body: jsonEncode(body))
        .timeout(_shortTimeout));
    // 201, not 200 — this is the one endpoint that creates something.
    return AuthSession.fromJson(_decode(resp, expect: 201));
  }

  Future<AuthSession> login({
    required String baseUrl,
    required String email,
    required String password,
  }) async {
    final resp = await _guard(() => _client
        .post(_uri(baseUrl, '/auth/login'),
            headers: _jsonHeaders(null),
            body: jsonEncode({'email': email.trim(), 'password': password}))
        .timeout(_shortTimeout));
    return AuthSession.fromJson(_decode(resp));
  }

  /// Re-read the signed-in account. Used on resume to confirm the token still
  /// works and to pick up a role change made server-side.
  Future<Account> me({required String baseUrl, required String token}) async {
    final resp = await _guard(() => _client
        .get(_uri(baseUrl, '/auth/me'), headers: _auth(token))
        .timeout(_shortTimeout));
    return Account.fromJson(_decode(resp));
  }

  /// Slide an active session forward. Returns a fresh token; the old one keeps
  /// working until it expires on its own.
  Future<AuthSession> refresh({
    required String baseUrl,
    required String token,
  }) async {
    final resp = await _guard(() => _client
        .post(_uri(baseUrl, '/auth/refresh'), headers: _auth(token))
        .timeout(_shortTimeout));
    return AuthSession.fromJson(_decode(resp));
  }

  // ----------------------------------------------------------------------- //
  // Scanning
  // ----------------------------------------------------------------------- //

  /// Photos -> full compliance report (extract + judge, in one call).
  ///
  ///  * [token] optional: with one the scan is filed and [ScanOutcome.scanId] is
  ///    populated; without one the verdict comes back and nothing is recorded.
  ///  * [serverMock] maps to the `mock` form field: true asks the backend to use
  ///    its offline OCR/Gemini mock path (works with no API key or heavy deps
  ///    installed); null lets the server auto-detect.
  ///  * [reference] / [context] are JSON-encoded exactly as the `/scan/image`
  ///    multipart form expects (e.g. a manual mm-per-pixel calibration).
  ///  * [save] set false to get a verdict without filing it, even when signed in.
  Future<ScanOutcome> scanImage({
    required String baseUrl,
    required List<LabelImage> images,
    String? token,
    Map<String, dynamic>? reference,
    Map<String, dynamic>? context,
    String? category,
    bool? serverMock,
    String? productName,
    String? note,
    String? location,
    bool? save,
  }) async {
    if (images.isEmpty) {
      throw ApiException('Add at least one label photo before scanning.');
    }
    final req = http.MultipartRequest('POST', _uri(baseUrl, '/scan/image'))
      ..headers.addAll(_auth(token));
    for (final img in images) {
      req.files.add(http.MultipartFile.fromBytes('images', img.bytes,
          filename: img.filename));
    }
    if (reference != null) req.fields['reference'] = jsonEncode(reference);
    if (context != null) req.fields['context'] = jsonEncode(context);
    if (category != null && category.isNotEmpty) req.fields['category'] = category;
    if (serverMock != null) req.fields['mock'] = serverMock ? 'true' : 'false';
    // Only sent when non-empty: the server treats an empty form field as absent
    // anyway, and sending '' would store a blank product name over a real one.
    _addIfPresent(req.fields, 'product_name', productName);
    _addIfPresent(req.fields, 'note', note);
    _addIfPresent(req.fields, 'location', location);
    if (save != null) req.fields['save'] = save ? 'true' : 'false';

    final resp = await _send(req, _scanTimeout);
    return ScanOutcome.fromJson(_decode(resp));
  }

  static void _addIfPresent(Map<String, String> fields, String key, String? value) {
    final v = (value ?? '').trim();
    if (v.isNotEmpty) fields[key] = v;
  }

  // ----------------------------------------------------------------------- //
  // History
  // ----------------------------------------------------------------------- //

  /// One page of inspection history. The server decides the scope: an officer
  /// gets the whole corpus, a consumer their own rows, and [ScanPage.scope] says
  /// which happened.
  Future<ScanPage> scans({
    required String baseUrl,
    required String token,
    String? verdict,
    String? category,
    String? search,
    int limit = 50,
    int offset = 0,
  }) async {
    final query = <String, String>{
      'limit': '$limit',
      'offset': '$offset',
      if (verdict != null && verdict.isNotEmpty) 'verdict': verdict,
      if (category != null && category.isNotEmpty) 'category': category,
      if (search != null && search.trim().isNotEmpty) 'search': search.trim(),
    };
    final resp = await _guard(() => _client
        .get(_uri(baseUrl, '/scans', query), headers: _auth(token))
        .timeout(_shortTimeout));
    return ScanPage.fromJson(_decode(resp));
  }

  /// One stored inspection including the verbatim report it produced.
  Future<SavedScan> scan({
    required String baseUrl,
    required String token,
    required String scanId,
  }) async {
    final resp = await _guard(() => _client
        .get(_uri(baseUrl, '/scans/${Uri.encodeComponent(scanId)}'),
            headers: _auth(token))
        .timeout(_shortTimeout));
    return SavedScan.fromJson(_decode(resp));
  }

  /// Delete a stored inspection. Returns normally on success; a 404 (which is
  /// also what another user's record looks like) surfaces as [ApiException].
  Future<void> deleteScan({
    required String baseUrl,
    required String token,
    required String scanId,
  }) async {
    final resp = await _guard(() => _client
        .delete(_uri(baseUrl, '/scans/${Uri.encodeComponent(scanId)}'),
            headers: _auth(token))
        .timeout(_shortTimeout));
    if (resp.statusCode != 204) _decode(resp);  // raises with the server's reason
  }

  /// Mint a short-lived link that opens this one report in any browser.
  ///
  /// The link is how a report gets printed: a phone cannot print, and a browser
  /// address bar cannot send an Authorization header. The ticket inside it is
  /// scoped to this inspection, expires in minutes, and is refused anywhere a
  /// session token is expected — so forwarding the link does not hand over the
  /// account.
  Future<ReportLink> shareReport({
    required String baseUrl,
    required String token,
    required String scanId,
    int minutes = 15,
  }) async {
    final resp = await _guard(() => _client
        .post(
            _uri(baseUrl, '/scans/${Uri.encodeComponent(scanId)}/share',
                {'minutes': '$minutes'}),
            headers: _auth(token))
        .timeout(_shortTimeout));
    return ReportLink.fromJson(_decode(resp));
  }

  /// Server-side aggregates over whatever corpus the caller may see.
  Future<ServerStats> stats({
    required String baseUrl,
    required String token,
  }) async {
    final resp = await _guard(() => _client
        .get(_uri(baseUrl, '/stats'), headers: _auth(token))
        .timeout(_shortTimeout));
    return ServerStats.fromJson(_decode(resp));
  }

  // ----------------------------------------------------------------------- //
  // Meta
  // ----------------------------------------------------------------------- //

  /// Liveness, how many rule packs are loaded, and whether history is available.
  Future<Map<String, dynamic>> health(String baseUrl) async {
    final resp = await _guard(
      () => _client.get(_uri(baseUrl, '/health')).timeout(_shortTimeout),
    );
    return _decode(resp);
  }

  /// Summary of every loaded rule pack (id, label, authority, scope, version).
  Future<List<Map<String, dynamic>>> rulePacks(String baseUrl) async {
    final resp = await _guard(
      () => _client.get(_uri(baseUrl, '/rulepacks')).timeout(_shortTimeout),
    );
    final decoded = json.decode(resp.body);
    final list = (decoded is Map && decoded['packs'] is List)
        ? decoded['packs'] as List
        : (decoded is List ? decoded : const []);
    return list.map((e) => (e as Map).cast<String, dynamic>()).toList();
  }

  // ----------------------------------------------------------------------- //
  // internals
  // ----------------------------------------------------------------------- //
  Future<http.Response> _send(http.MultipartRequest req, Duration t) async {
    return _guard(() async {
      final streamed = await req.send().timeout(t);
      return http.Response.fromStream(streamed);
    });
  }

  Future<http.Response> _guard(Future<http.Response> Function() run) async {
    try {
      return await run();
    } on TimeoutException {
      throw ApiException(
          'The server took too long to respond. A real OCR + Gemini scan can '
          'take a while on the first request — or check the API address in Settings.');
    } on SocketException {
      throw ApiException(
          "Can't reach the backend. Is uvicorn running, and is the API address "
          'in Settings correct for your device? (Android emulator: 10.0.2.2, '
          'physical device: your Mac\'s LAN IP.)');
    } on http.ClientException catch (e) {
      throw ApiException('Network error: ${e.message}');
    }
  }

  Map<String, dynamic> _decode(http.Response resp, {int expect = 200}) {
    if (resp.statusCode == expect) {
      final decoded = json.decode(resp.body);
      if (decoded is Map<String, dynamic>) return decoded;
      throw ApiException('Unexpected response shape from the server.');
    }
    // FastAPI errors are {"detail": "..."}; a 422 is instead a list of field
    // errors, which is a developer-facing shape — flattened here so the user
    // sees something readable rather than raw JSON.
    String detail = 'Request failed (HTTP ${resp.statusCode}).';
    try {
      final d = json.decode(resp.body);
      if (d is Map && d['detail'] != null) {
        final raw = d['detail'];
        detail = raw is List ? _flattenValidation(raw) : raw.toString();
      }
    } catch (_) {/* keep the generic message */}

    // Only nudge about the mock toggle where it is actually the likely fix.
    // A 503 from a history endpoint means "no database", and telling someone to
    // turn on the mock pipeline would be advice that cannot possibly help.
    if (resp.statusCode == 503 && resp.request?.url.path.contains('/scan') == true) {
      detail = '$detail\n\nTip: turn on "Use server mock pipeline" in Settings to '
          'run without OCR/Gemini installed.';
    }
    throw ApiException(detail, statusCode: resp.statusCode);
  }

  static String _flattenValidation(List<dynamic> errors) {
    final parts = <String>[];
    for (final e in errors) {
      if (e is Map) {
        final loc = (e['loc'] as List?)?.map((s) => s.toString()).toList() ?? const [];
        final field = loc.isEmpty ? '' : loc.last;
        final msg = (e['msg'] ?? 'is invalid').toString();
        parts.add(field.isEmpty ? msg : '$field: $msg');
      }
    }
    return parts.isEmpty ? 'The server rejected that request.' : parts.join('\n');
  }

  void dispose() => _client.close();
}
