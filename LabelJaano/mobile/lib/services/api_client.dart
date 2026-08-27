import 'dart:async';
import 'dart:convert';
import 'dart:io' show SocketException;
import 'dart:typed_data';

import 'package:http/http.dart' as http;

import '../models/compliance_report.dart';

/// One image to upload, with the filename the server will see.
class LabelImage {
  LabelImage(this.bytes, this.filename);
  final Uint8List bytes;
  final String filename;
}

/// A friendly, user-facing failure. Screens show [message] directly.
class ApiException implements Exception {
  ApiException(this.message, {this.statusCode});
  final String message;
  final int? statusCode;
  @override
  String toString() => message;
}

/// Thin client over the Label Jaano FastAPI backend. The only endpoint the app
/// truly depends on is `POST /scan/image` (photos -> ComplianceReport); the
/// rest power the Settings health check and the rule-pack browser.
///
/// The base URL is passed in per-call so it can be changed live from Settings
/// without rebuilding the client.
class ApiClient {
  ApiClient({http.Client? client}) : _client = client ?? http.Client();

  final http.Client _client;

  static const Duration _scanTimeout = Duration(seconds: 90);
  static const Duration _shortTimeout = Duration(seconds: 12);

  Uri _uri(String base, String path) => Uri.parse('${_normalise(base)}$path');

  static String _normalise(String base) {
    var b = base.trim();
    if (b.endsWith('/')) b = b.substring(0, b.length - 1);
    if (!b.startsWith('http://') && !b.startsWith('https://')) b = 'http://$b';
    return b;
  }

  /// Photos -> full compliance report (extract + judge, in one call).
  ///
  ///  * [serverMock] maps to the `mock` form field: true asks the backend to
  ///    use its offline OCR/Gemini mock path (works with no API key / heavy
  ///    deps installed); null lets the server auto-detect.
  ///  * [reference] / [context] are JSON-encoded exactly as the `/scan/image`
  ///    multipart form expects (e.g. a manual mm-per-pixel calibration).
  Future<ComplianceReport> scanImage({
    required String baseUrl,
    required List<LabelImage> images,
    Map<String, dynamic>? reference,
    Map<String, dynamic>? context,
    String? category,
    bool? serverMock,
  }) async {
    if (images.isEmpty) {
      throw ApiException('Add at least one label photo before scanning.');
    }
    final req = http.MultipartRequest('POST', _uri(baseUrl, '/scan/image'));
    for (final img in images) {
      req.files.add(http.MultipartFile.fromBytes('images', img.bytes,
          filename: img.filename));
    }
    if (reference != null) req.fields['reference'] = jsonEncode(reference);
    if (context != null) req.fields['context'] = jsonEncode(context);
    if (category != null && category.isNotEmpty) req.fields['category'] = category;
    if (serverMock != null) req.fields['mock'] = serverMock ? 'true' : 'false';

    final resp = await _send(req, _scanTimeout);
    final body = _decode(resp);
    return ComplianceReport.fromJson(body);
  }

  /// Liveness + how many rule packs the server has loaded.
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

  Map<String, dynamic> _decode(http.Response resp) {
    if (resp.statusCode == 200) {
      final decoded = json.decode(resp.body);
      if (decoded is Map<String, dynamic>) return decoded;
      throw ApiException('Unexpected response shape from the server.');
    }
    // FastAPI errors are {"detail": "..."} (or a validation array).
    String detail = 'Request failed (HTTP ${resp.statusCode}).';
    try {
      final d = json.decode(resp.body);
      if (d is Map && d['detail'] != null) detail = d['detail'].toString();
    } catch (_) {/* keep the generic message */}

    if (resp.statusCode == 503) {
      detail = '$detail\n\nTip: turn on "Use server mock pipeline" in Settings to '
          'run without OCR/Gemini installed.';
    }
    throw ApiException(detail, statusCode: resp.statusCode);
  }

  void dispose() => _client.close();
}
