import 'dart:typed_data';

import 'compliance_report.dart';

/// One inspection performed in this session: the photos the officer captured,
/// the report the backend returned, and when. Held in memory by [ScanStore];
/// the dashboard and officer queue are aggregated from a list of these.
///
/// (Persisting these across launches — a local SQLite/Hive history — is the
/// natural next step; see mobile/README.md.)
class ScanRecord {
  ScanRecord({
    required this.id,
    required this.capturedAt,
    required this.report,
    this.thumbnails = const [],
    this.note,
    this.serverMock = false,
  });

  final String id;
  final DateTime capturedAt;
  final ComplianceReport report;

  /// Front (and optionally back) panel image bytes, kept only for this session
  /// so the report and queue can show what was scanned.
  final List<Uint8List> thumbnails;

  /// Optional officer note (e.g. shop name / location) — free text for the demo.
  final String? note;

  /// True if this scan was produced by the backend's offline mock pipeline
  /// rather than live OCR + Gemini. Surfaced so a demo verdict is never
  /// mistaken for a live model read.
  final bool serverMock;

  Verdict get verdict => report.verdict;
  double get score => report.score;
}
