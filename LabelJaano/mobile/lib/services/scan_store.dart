import 'package:flutter/foundation.dart';

import '../models/compliance_report.dart';
import '../models/scan_record.dart';

/// In-memory store of every inspection performed this session. The Dashboard
/// and Officer Queue are pure projections of this list, so a new scan updates
/// both instantly. (Swap this for a persisted repository — SQLite/Hive — to
/// keep history across launches; the widget layer wouldn't change.)
class ScanStore extends ChangeNotifier {
  final List<ScanRecord> _records = [];

  List<ScanRecord> get records => List.unmodifiable(_records);
  bool get isEmpty => _records.isEmpty;
  int get total => _records.length;

  /// Most recent first — the order both the queue and "recent activity" use.
  List<ScanRecord> get recent {
    final sorted = [..._records]..sort((a, b) => b.capturedAt.compareTo(a.capturedAt));
    return sorted;
  }

  void add(ScanRecord record) {
    _records.add(record);
    notifyListeners();
  }

  void remove(String id) {
    _records.removeWhere((r) => r.id == id);
    notifyListeners();
  }

  void clear() {
    _records.clear();
    notifyListeners();
  }

  ScanRecord? byId(String id) {
    for (final r in _records) {
      if (r.id == id) return r;
    }
    return null;
  }

  // ---- aggregates for the dashboard ------------------------------------- //

  int countByVerdict(Verdict v) => _records.where((r) => r.verdict == v).length;

  Map<Verdict, int> get verdictCounts => {
        Verdict.compliant: countByVerdict(Verdict.compliant),
        Verdict.needsReview: countByVerdict(Verdict.needsReview),
        Verdict.nonCompliant: countByVerdict(Verdict.nonCompliant),
      };

  /// Inspections that actually evaluated a label (a verdict with a real score).
  /// "No label detected" reads are mis-aimed photos, not inspections, so they
  /// are excluded from the score/rate aggregates below.
  List<ScanRecord> get _scored => _records
      .where((r) =>
          r.verdict == Verdict.compliant ||
          r.verdict == Verdict.needsReview ||
          r.verdict == Verdict.nonCompliant)
      .toList();

  /// Average compliance score across all scored inspections (0 when none).
  double get averageScore {
    final scored = _scored;
    if (scored.isEmpty) return 0;
    final sum = scored.fold<double>(0, (a, r) => a + r.score);
    return sum / scored.length;
  }

  /// Total open violations across all inspections, split by severity.
  Map<Severity, int> get violationTotals {
    var critical = 0, major = 0, minor = 0;
    for (final r in _records) {
      critical += r.report.summary.critical;
      major += r.report.summary.major;
      minor += r.report.summary.minor;
    }
    return {
      Severity.critical: critical,
      Severity.major: major,
      Severity.minor: minor,
    };
  }

  int get totalViolations =>
      violationTotals.values.fold<int>(0, (a, b) => a + b);

  /// Share of scored inspections that came back fully compliant, as a
  /// percentage. Excludes "no label detected" reads (not real inspections).
  double get complianceRate {
    final scored = _scored;
    if (scored.isEmpty) return 0;
    return 100.0 * countByVerdict(Verdict.compliant) / scored.length;
  }
}
