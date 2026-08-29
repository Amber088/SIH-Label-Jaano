import 'dart:ui' show Color;

import '../core/theme.dart';

/// Dart mirror of the backend's `ComplianceReport.to_dict()` (see
/// backend/rule_engine/models.py). Field names and JSON keys match the API 1:1
/// so `ComplianceReport.fromJson(response)` is a straight decode.

enum Verdict {
  compliant,
  needsReview,
  nonCompliant,
  noLabel,
  unknown;

  static Verdict fromJson(String? v) {
    switch (v) {
      case 'compliant':
        return Verdict.compliant;
      case 'needs_review':
        return Verdict.needsReview;
      case 'non_compliant':
        return Verdict.nonCompliant;
      case 'no_label_detected':
        return Verdict.noLabel;
      default:
        return Verdict.unknown;
    }
  }

  String get label => switch (this) {
        Verdict.compliant => 'Compliant',
        Verdict.needsReview => 'Needs review',
        Verdict.nonCompliant => 'Non-compliant',
        Verdict.noLabel => 'No label detected',
        Verdict.unknown => 'Unknown',
      };

  /// A short, officer-facing gloss of what the verdict means.
  String get gloss => switch (this) {
        Verdict.compliant => 'All mandatory declarations present and valid',
        Verdict.needsReview => 'Major gaps — manual review recommended',
        Verdict.nonCompliant => 'Critical declarations missing or invalid',
        Verdict.noLabel =>
          'This image doesn’t appear to be a packaged-commodity label',
        Verdict.unknown => 'Verdict unavailable',
      };

  Color get color => switch (this) {
        Verdict.compliant => Palette.green,
        Verdict.needsReview => Palette.amber,
        Verdict.nonCompliant => Palette.red,
        Verdict.noLabel => Palette.muted,
        Verdict.unknown => Palette.muted,
      };

  Color get tint => switch (this) {
        Verdict.compliant => Palette.greenTint,
        Verdict.needsReview => Palette.amberTint,
        Verdict.nonCompliant => Palette.redTint,
        Verdict.noLabel => Palette.hairline,
        Verdict.unknown => Palette.hairline,
      };
}

enum Severity {
  critical,
  major,
  minor,
  unknown;

  static Severity fromJson(String? v) => switch (v) {
        'critical' => Severity.critical,
        'major' => Severity.major,
        'minor' => Severity.minor,
        _ => Severity.unknown,
      };

  String get label => switch (this) {
        Severity.critical => 'Critical',
        Severity.major => 'Major',
        Severity.minor => 'Minor',
        Severity.unknown => 'Unknown',
      };

  Color get color => switch (this) {
        Severity.critical => Palette.red,
        Severity.major => Palette.amber,
        Severity.minor => Palette.muted,
        Severity.unknown => Palette.muted,
      };
}

enum Outcome {
  pass,
  fail,
  skip,
  unknown;

  static Outcome fromJson(String? v) => switch (v) {
        'pass' => Outcome.pass,
        'fail' => Outcome.fail,
        'skip' => Outcome.skip,
        _ => Outcome.unknown,
      };

  String get label => switch (this) {
        Outcome.pass => 'Pass',
        Outcome.fail => 'Fail',
        Outcome.skip => 'Skipped',
        Outcome.unknown => '—',
      };

  Color get color => switch (this) {
        Outcome.pass => Palette.green,
        Outcome.fail => Palette.red,
        Outcome.skip => Palette.faint,
        Outcome.unknown => Palette.faint,
      };
}

/// A failed check, surfaced to the officer with its exact legal citation.
class Violation {
  Violation({
    required this.declarationId,
    required this.declarationLabel,
    required this.legalReference,
    required this.severity,
    required this.checkType,
    required this.message,
    this.detail,
  });

  final String declarationId;
  final String declarationLabel;
  final String legalReference;
  final Severity severity;
  final String checkType;
  final String message;
  final String? detail;

  factory Violation.fromJson(Map<String, dynamic> j) => Violation(
        declarationId: (j['declaration_id'] ?? '') as String,
        declarationLabel: (j['declaration_label'] ?? '') as String,
        legalReference: (j['legal_reference'] ?? '') as String,
        severity: Severity.fromJson(j['severity'] as String?),
        checkType: (j['check_type'] ?? '') as String,
        message: (j['message'] ?? '') as String,
        detail: j['detail'] as String?,
      );
}

/// Every check the engine ran — pass, fail, or skip — so the report can show
/// the full audited picture, not just the failures.
class CheckResult {
  CheckResult({
    required this.declarationId,
    required this.declarationLabel,
    required this.legalReference,
    required this.severity,
    required this.checkType,
    required this.outcome,
    this.message,
    this.detail,
  });

  final String declarationId;
  final String declarationLabel;
  final String legalReference;
  final Severity severity;
  final String checkType;
  final Outcome outcome;
  final String? message;
  final String? detail;

  factory CheckResult.fromJson(Map<String, dynamic> j) => CheckResult(
        declarationId: (j['declaration_id'] ?? '') as String,
        declarationLabel: (j['declaration_label'] ?? '') as String,
        legalReference: (j['legal_reference'] ?? '') as String,
        severity: Severity.fromJson(j['severity'] as String?),
        checkType: (j['check_type'] ?? '') as String,
        outcome: Outcome.fromJson(j['outcome'] as String?),
        message: j['message'] as String?,
        detail: j['detail'] as String?,
      );
}

/// A Tier-2, reference-only provision: a legal requirement that genuinely applies
/// to the product but that a label photo CANNOT verify (compositional minima,
/// additive use-limits, heavy-metal caps, lab-only safety parameters). The backend
/// keeps these on a separate data path so they can never move the score or verdict —
/// they ride along purely as "applicable standard; verify in lab" context.
class ReferenceStandard {
  ReferenceStandard({
    required this.id,
    required this.label,
    required this.legalReference,
    this.authority = '',
    this.note = '',
  });

  final String id;
  final String label;
  final String legalReference;
  final String authority;
  final String note;

  factory ReferenceStandard.fromJson(Map<String, dynamic> j) => ReferenceStandard(
        id: (j['id'] ?? '') as String,
        label: (j['label'] ?? '') as String,
        legalReference: (j['legal_reference'] ?? '') as String,
        authority: (j['authority'] ?? '') as String,
        note: (j['note'] ?? '') as String,
      );
}

class ReportSummary {
  ReportSummary({
    required this.checksTotal,
    required this.passed,
    required this.failed,
    required this.skipped,
    required this.violationsTotal,
    required this.critical,
    required this.major,
    required this.minor,
  });

  final int checksTotal;
  final int passed;
  final int failed;
  final int skipped;
  final int violationsTotal;
  final int critical;
  final int major;
  final int minor;

  factory ReportSummary.fromJson(Map<String, dynamic> j) {
    final sev = (j['violations_by_severity'] as Map?)?.cast<String, dynamic>() ?? const {};
    int n(dynamic v) => (v is num) ? v.toInt() : 0;
    return ReportSummary(
      checksTotal: n(j['checks_total']),
      passed: n(j['passed']),
      failed: n(j['failed']),
      skipped: n(j['skipped']),
      violationsTotal: n(j['violations_total']),
      critical: n(sev['critical']),
      major: n(sev['major']),
      minor: n(sev['minor']),
    );
  }

  factory ReportSummary.empty() => ReportSummary(
        checksTotal: 0,
        passed: 0,
        failed: 0,
        skipped: 0,
        violationsTotal: 0,
        critical: 0,
        major: 0,
        minor: 0,
      );
}

class ComplianceReport {
  ComplianceReport({
    required this.verdict,
    required this.score,
    required this.category,
    required this.packsApplied,
    required this.summary,
    required this.violations,
    required this.results,
    this.referenceStandards = const [],
  });

  final Verdict verdict;
  final double score;
  final String category;
  final List<String> packsApplied;
  final ReportSummary summary;
  final List<Violation> violations;
  final List<CheckResult> results;
  final List<ReferenceStandard> referenceStandards;

  factory ComplianceReport.fromJson(Map<String, dynamic> j) => ComplianceReport(
        verdict: Verdict.fromJson(j['verdict'] as String?),
        score: (j['score'] is num) ? (j['score'] as num).toDouble() : 0.0,
        category: (j['category'] ?? 'unknown') as String,
        packsApplied:
            ((j['packs_applied'] as List?) ?? const []).map((e) => e.toString()).toList(),
        summary: ReportSummary.fromJson(
            (j['summary'] as Map?)?.cast<String, dynamic>() ?? const {}),
        violations: ((j['violations'] as List?) ?? const [])
            .map((e) => Violation.fromJson((e as Map).cast<String, dynamic>()))
            .toList(),
        results: ((j['results'] as List?) ?? const [])
            .map((e) => CheckResult.fromJson((e as Map).cast<String, dynamic>()))
            .toList(),
        referenceStandards: ((j['reference_standards'] as List?) ?? const [])
            .map((e) => ReferenceStandard.fromJson((e as Map).cast<String, dynamic>()))
            .toList(),
      );

  /// A friendly title for the category (e.g. "packaged_food" -> "Packaged food").
  String get categoryLabel => prettyCategory(category);
}

/// "packaged_food" -> "Packaged food". Top-level because a history row carries a
/// category without carrying a report, and two copies of this would eventually
/// disagree about how to spell a category name on two different screens.
String prettyCategory(String category) {
  if (category.isEmpty || category == 'unknown') return 'Uncategorised';
  final words = category.replaceAll('_', ' ').split(' ');
  return words
      .map((w) => w.isEmpty ? w : '${w[0].toUpperCase()}${w.substring(1)}')
      .join(' ');
}
