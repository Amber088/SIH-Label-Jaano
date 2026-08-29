import 'compliance_report.dart';

/// Dart mirrors of the server's history and aggregate shapes: `ScanSummaryOut`,
/// `ScanDetailOut`, `ScanListOut`, `ReportLinkOut` and `StatsOut` in
/// backend/app/schemas.py.
///
/// These are deliberately separate from [ScanRecord], which is the app's own
/// local notion of "a scan I just took" and carries things the server never sees
/// (the photo bytes). A saved scan is the server's record; hydrating one into a
/// [ScanRecord] is a one-way conversion, done in `ScanRecord.fromSaved`.

/// One stored inspection. [report] is populated only by the detail endpoint —
/// the list view omits the report body on purpose, because a page of fifty full
/// reports is a lot of JSON to move over a phone connection to draw a list of
/// verdict chips.
class SavedScan {
  const SavedScan({
    required this.id,
    required this.createdAt,
    required this.verdict,
    required this.score,
    required this.category,
    this.userId,
    this.packsApplied = const [],
    this.checksTotal = 0,
    this.violationsTotal = 0,
    this.violationsBySeverity = const {},
    this.source = 'json',
    this.mock = false,
    this.productName,
    this.note,
    this.location,
    this.report,
    this.filed = true,
  });

  final String id;

  /// Server timestamp, ISO 8601 UTC. Kept as the raw string as well as parsed
  /// (see [capturedAt]) so a value the app cannot parse still round-trips.
  final String createdAt;
  final Verdict verdict;
  final double score;
  final String category;

  /// Who filed it. Only meaningful to an officer, whose list spans other people's
  /// inspections; a consumer's rows are all their own.
  final String? userId;
  final List<String> packsApplied;
  final int checksTotal;
  final int violationsTotal;

  /// Open violations split by severity, read from the stored report's summary.
  /// Present on a list row as well as a detail row, so the dashboard can draw a
  /// severity breakdown without pulling every report body down the wire.
  final Map<Severity, int> violationsBySeverity;

  /// 'image' or 'json' — how the label reached the engine.
  final String source;

  /// True when the values were extracted by the offline mock rather than a real
  /// read. Surfaced in the UI because a mock verdict is not evidence.
  final bool mock;
  final String? productName;
  final String? note;
  final String? location;

  /// The verbatim report as it was shown at the time. Detail endpoint only.
  final ComplianceReport? report;

  /// Whether the server holds this row.
  ///
  /// Always true for anything decoded from the API — the row exists because it
  /// was filed. False only for a local scan the queue is displaying in the same
  /// list (anonymous, `save=false`, or a storage failure), which is why export,
  /// share and server-side delete must be hidden for it rather than failing when
  /// tapped.
  final bool filed;

  factory SavedScan.fromJson(Map<String, dynamic> json) {
    final summary = (json['summary'] as Map?)?.cast<String, dynamic>() ?? const {};
    final rawReport = (json['report'] as Map?)?.cast<String, dynamic>();
    return SavedScan(
      id: (json['id'] ?? '').toString(),
      createdAt: (json['created_at'] ?? '').toString(),
      verdict: Verdict.fromJson(json['verdict'] as String?),
      score: (json['score'] as num?)?.toDouble() ?? 0,
      category: (json['category'] ?? 'unknown').toString(),
      userId: json['user_id'] as String?,
      packsApplied:
          (json['packs_applied'] as List?)?.map((e) => e.toString()).toList() ??
              const [],
      checksTotal: (summary['checks_total'] as num?)?.toInt() ?? 0,
      violationsTotal: (summary['violations_total'] as num?)?.toInt() ?? 0,
      violationsBySeverity: _severities(summary['violations_by_severity']),
      source: (json['source'] ?? 'json').toString(),
      mock: json['mock'] == true,
      productName: json['product_name'] as String?,
      note: json['note'] as String?,
      location: json['location'] as String?,
      report: rawReport == null ? null : ComplianceReport.fromJson(rawReport),
    );
  }

  /// Parsed [createdAt], or null when the server sent something unexpected.
  /// The API returns UTC; `toLocal` puts it in the officer's own clock, which is
  /// the only time that means anything on an inspection record.
  DateTime? get capturedAt {
    final parsed = DateTime.tryParse(createdAt);
    return parsed?.isUtc == true ? parsed!.toLocal() : parsed;
  }

  static Map<Severity, int> _severities(Object? raw) {
    final out = <Severity, int>{};
    if (raw is Map) {
      raw.forEach((key, value) {
        final s = Severity.fromJson(key.toString());
        out[s] = (out[s] ?? 0) + ((value as num?)?.toInt() ?? 0);
      });
    }
    return out;
  }

  int countOfSeverity(Severity s) => violationsBySeverity[s] ?? 0;

  /// True when this row is scored — i.e. a label was actually assessed. A
  /// `no_label_detected` read is a mis-aimed photo, not a product scoring zero,
  /// and averaging it in would quietly slander every seller in the corpus.
  bool get isScored =>
      verdict == Verdict.compliant ||
      verdict == Verdict.needsReview ||
      verdict == Verdict.nonCompliant;

  /// What to head the row with. Falls back to the category so a scan filed
  /// without a product name is still identifiable at a glance.
  String get title {
    final name = (productName ?? '').trim();
    if (name.isNotEmpty) return name;
    return prettyCategory(category);
  }

  bool get hasReport => report != null;
}

/// One page of history, plus the total behind it so the UI can say "50 of 214"
/// rather than pretending the page is the whole corpus.
class ScanPage {
  const ScanPage({
    required this.items,
    required this.total,
    required this.limit,
    required this.offset,
    required this.scope,
  });

  final List<SavedScan> items;
  final int total;
  final int limit;
  final int offset;

  /// 'own' or 'all' — which corpus the server actually searched. Displayed rather
  /// than inferred from the role, so the app reports what happened instead of
  /// what it expected to happen.
  final String scope;

  static const ScanPage empty =
      ScanPage(items: [], total: 0, limit: 50, offset: 0, scope: 'own');

  factory ScanPage.fromJson(Map<String, dynamic> json) => ScanPage(
        items: ((json['items'] as List?) ?? const [])
            .map((e) => SavedScan.fromJson((e as Map).cast<String, dynamic>()))
            .toList(),
        total: (json['total'] as num?)?.toInt() ?? 0,
        limit: (json['limit'] as num?)?.toInt() ?? 50,
        offset: (json['offset'] as num?)?.toInt() ?? 0,
        scope: (json['scope'] ?? 'own').toString(),
      );

  bool get spansEveryone => scope == 'all';

  /// Whether another page exists after this one.
  bool get hasMore => offset + items.length < total;
}

/// A short-lived link to one report, from `POST /scans/{id}/share`.
///
/// [path] is relative — the server declines to guess its own public origin — so
/// [urlFrom] joins it to whatever base address the app actually dialled.
class ReportLink {
  const ReportLink({
    required this.scanId,
    required this.path,
    required this.ticket,
    required this.expiresAt,
    required this.expiresInSeconds,
  });

  final String scanId;
  final String path;
  final String ticket;
  final String expiresAt;
  final int expiresInSeconds;

  factory ReportLink.fromJson(Map<String, dynamic> json) => ReportLink(
        scanId: (json['scan_id'] ?? '').toString(),
        path: (json['path'] ?? '').toString(),
        ticket: (json['ticket'] ?? '').toString(),
        expiresAt: (json['expires_at'] ?? '').toString(),
        expiresInSeconds: (json['expires_in_seconds'] as num?)?.toInt() ?? 0,
      );

  String urlFrom(String baseUrl) {
    final base = baseUrl.trim().replaceAll(RegExp(r'/+$'), '');
    return '$base$path';
  }

  /// "15 minutes" / "2 hours" — for the line under the copied link.
  String get validFor {
    final minutes = (expiresInSeconds / 60).round();
    if (minutes < 60) return '$minutes minute${minutes == 1 ? '' : 's'}';
    final hours = minutes / 60;
    final rounded = hours == hours.roundToDouble()
        ? hours.round().toString()
        : hours.toStringAsFixed(1);
    return '$rounded hour${rounded == '1' ? '' : 's'}';
  }
}

/// One row of `stats.by_category`.
class CategoryStat {
  const CategoryStat({
    required this.category,
    required this.scans,
    required this.averageScore,
  });

  final String category;
  final int scans;
  final double averageScore;

  factory CategoryStat.fromJson(Map<String, dynamic> json) => CategoryStat(
        category: (json['category'] ?? 'unknown').toString(),
        scans: (json['scans'] as num?)?.toInt() ?? 0,
        averageScore: (json['average_score'] as num?)?.toDouble() ?? 0,
      );

  String get label => prettyCategory(category);
}

/// One row of `stats.top_violations` — the enforcement-intelligence payload. This
/// is the answer to "which declaration do sellers breach most often", which no
/// single label can tell you.
class TopViolation {
  const TopViolation({
    required this.declarationId,
    required this.declarationLabel,
    required this.legalReference,
    required this.severity,
    required this.occurrences,
    required this.scansAffected,
  });

  final String declarationId;
  final String declarationLabel;
  final String legalReference;
  final Severity severity;

  /// Failed checks. One label can fail the same declaration more than once.
  final int occurrences;

  /// Distinct products in breach — the number to quote, because it counts
  /// sellers rather than checks.
  final int scansAffected;

  factory TopViolation.fromJson(Map<String, dynamic> json) => TopViolation(
        declarationId: (json['declaration_id'] ?? '').toString(),
        declarationLabel: (json['declaration_label'] ?? '').toString(),
        legalReference: (json['legal_reference'] ?? '').toString(),
        severity: Severity.fromJson(json['severity'] as String?),
        occurrences: (json['occurrences'] as num?)?.toInt() ?? 0,
        scansAffected: (json['scans_affected'] as num?)?.toInt() ?? 0,
      );
}

/// Server-side aggregates over whatever corpus the caller may see.
class ServerStats {
  const ServerStats({
    required this.totalScans,
    required this.scoredScans,
    required this.byVerdict,
    required this.averageScore,
    required this.complianceRate,
    required this.violationsBySeverity,
    required this.violationsTotal,
    required this.byCategory,
    required this.topViolations,
    required this.scope,
  });

  final int totalScans;

  /// Scans that carry a score. `no_label_detected` reads are excluded — a photo
  /// that was not a label at all is not a product scoring zero, and averaging it
  /// in would quietly libel every seller in the corpus.
  final int scoredScans;
  final Map<Verdict, int> byVerdict;
  final double averageScore;
  final double complianceRate;
  final Map<Severity, int> violationsBySeverity;
  final int violationsTotal;
  final List<CategoryStat> byCategory;
  final List<TopViolation> topViolations;
  final String scope;

  factory ServerStats.fromJson(Map<String, dynamic> json) {
    final verdicts = <Verdict, int>{};
    ((json['by_verdict'] as Map?) ?? const {}).forEach((key, value) {
      final v = Verdict.fromJson(key.toString());
      verdicts[v] = (verdicts[v] ?? 0) + ((value as num?)?.toInt() ?? 0);
    });
    final severities = <Severity, int>{};
    ((json['violations_by_severity'] as Map?) ?? const {}).forEach((key, value) {
      final s = Severity.fromJson(key.toString());
      severities[s] = (severities[s] ?? 0) + ((value as num?)?.toInt() ?? 0);
    });
    return ServerStats(
      totalScans: (json['total_scans'] as num?)?.toInt() ?? 0,
      scoredScans: (json['scored_scans'] as num?)?.toInt() ?? 0,
      byVerdict: verdicts,
      averageScore: (json['average_score'] as num?)?.toDouble() ?? 0,
      complianceRate: (json['compliance_rate'] as num?)?.toDouble() ?? 0,
      violationsBySeverity: severities,
      violationsTotal: (json['violations_total'] as num?)?.toInt() ?? 0,
      byCategory: ((json['by_category'] as List?) ?? const [])
          .map((e) => CategoryStat.fromJson((e as Map).cast<String, dynamic>()))
          .toList(),
      topViolations: ((json['top_violations'] as List?) ?? const [])
          .map((e) => TopViolation.fromJson((e as Map).cast<String, dynamic>()))
          .toList(),
      scope: (json['scope'] ?? 'own').toString(),
    );
  }

  bool get spansEveryone => scope == 'all';
  bool get isEmpty => totalScans == 0;

  int countOf(Verdict v) => byVerdict[v] ?? 0;
  int countOfSeverity(Severity s) => violationsBySeverity[s] ?? 0;
}
