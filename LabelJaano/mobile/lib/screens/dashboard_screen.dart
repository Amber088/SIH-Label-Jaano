import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/theme.dart';
import '../models/compliance_report.dart';
import '../models/saved_scan.dart';
import '../services/scan_store.dart';
import '../services/session.dart';
import '../widgets/brand.dart';
import '../widgets/charts.dart';
import '../widgets/common.dart';
import '../widgets/scan_tile.dart';
import '../widgets/stat_card.dart';
import 'report_screen.dart';
import 'sign_in_screen.dart';

/// The supervisor's-eye view: the caseload at a glance — how many packages were
/// inspected, the average compliance score, the verdict mix, open violations by
/// severity, the declarations sellers breach most often, and a tap-through
/// recent-activity feed.
///
/// The numbers come from the server when signed in and from the device otherwise,
/// and the difference is not cosmetic. `GET /stats` aggregates over the caller's
/// entire corpus — for an officer, every inspection anyone has filed — and answers
/// the one question no single label can: which declaration is breached most. The
/// device-local fallback is the same shape computed over the loaded rows, which is
/// exactly right for a consumer and honest about being narrower.
class DashboardScreen extends StatefulWidget {
  const DashboardScreen({super.key, required this.onScan, required this.onSeeQueue});

  final VoidCallback onScan;
  final VoidCallback onSeeQueue;

  @override
  State<DashboardScreen> createState() => _DashboardScreenState();
}

class _DashboardScreenState extends State<DashboardScreen> {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      final store = context.read<ScanStore>();
      if (store.syncedAt == null) store.sync();
    });
  }

  Future<void> _refresh() => context.read<ScanStore>().sync();

  @override
  Widget build(BuildContext context) {
    final store = context.watch<ScanStore>();
    final session = context.watch<Session>();
    final stats = store.stats;

    // Server figures when we have them, device figures when we don't. Assembled
    // once here so every card below reads from a single set of numbers — mixing
    // the two would put a corpus-wide average next to a device-local count and
    // quietly invite the wrong conclusion.
    final view = _Figures.from(store: store, stats: stats);

    return RefreshIndicator(
      color: Palette.brassDeep,
      onRefresh: _refresh,
      child: CustomScrollView(
        // Always scrollable, even when the content fits — otherwise pull-to-refresh
        // is dead in the empty state, which is exactly where someone waiting for
        // their history to arrive will try it.
        physics: const AlwaysScrollableScrollPhysics(),
        slivers: [
          SliverToBoxAdapter(child: _Hero(figures: view, session: session)),
          if (view.inspections == 0)
            SliverFillRemaining(
              hasScrollBody: false,
              child: EmptyState(
                icon: Icons.center_focus_strong_rounded,
                title: 'No inspections yet',
                message:
                    'Scan a package label to get a verdict in seconds. Your results '
                    'build the dashboard and the officer queue as you go.',
                action: FilledButton.icon(
                  onPressed: widget.onScan,
                  icon: const Icon(Icons.camera_alt_rounded, size: 20),
                  label: const Text('Scan a label'),
                ),
              ),
            )
          else
            SliverPadding(
              padding: kPagePadding,
              sliver: SliverList(
                delegate: SliverChildListDelegate([
                  _statGrid(view),
                  const SizedBox(height: 10),
                  Text(view.provenance,
                      style: LabelJaanoTheme.readout(size: 11, color: Palette.faint)),
                  const SizedBox(height: 24),
                  const SectionHeader('Verdict mix'),
                  _card(child: VerdictDonut(counts: view.verdictCounts)),
                  const SizedBox(height: 24),
                  const SectionHeader('Open violations by severity'),
                  _card(child: SeverityBar(totals: view.severityTotals)),
                  if (stats != null && stats.topViolations.isNotEmpty) ...[
                    const SizedBox(height: 24),
                    SectionHeader(
                      'Most breached declarations',
                      trailing: Text(stats.spansEveryone ? 'ALL ACCOUNTS' : 'YOUR SCANS',
                          style: LabelJaanoTheme.eyebrow(color: Palette.brassDeep)),
                    ),
                    _TopViolations(rows: stats.topViolations.take(5).toList()),
                  ],
                  if (stats != null && stats.byCategory.length > 1) ...[
                    const SizedBox(height: 24),
                    const SectionHeader('By category'),
                    _CategoryTable(rows: stats.byCategory),
                  ],
                  const SizedBox(height: 24),
                  SectionHeader(
                    'Recent activity',
                    trailing: GestureDetector(
                      onTap: widget.onSeeQueue,
                      child: Text('View queue',
                          style: LabelJaanoTheme.display(
                              size: 12.5,
                              weight: FontWeight.w600,
                              color: Palette.brassDeep)),
                    ),
                  ),
                  ...store.recent.take(5).map(
                        (row) => Padding(
                          padding: const EdgeInsets.only(bottom: 10),
                          child: ScanTile(
                            row: row,
                            thumbnail: _thumbnailFor(store, row),
                            onTap: () => Navigator.of(context).push(
                              MaterialPageRoute(
                                  builder: (_) => ReportScreen(recordId: row.id)),
                            ),
                          ),
                        ),
                      ),
                  if (!session.isSignedIn) ...[
                    const SizedBox(height: 10),
                    _SignInNudge(
                      onTap: () => Navigator.of(context).push(
                        MaterialPageRoute(builder: (_) => const SignInScreen()),
                      ),
                    ),
                  ],
                ]),
              ),
            ),
        ],
      ),
    );
  }

  static Uint8List? _thumbnailFor(ScanStore store, SavedScan row) {
    final shots = store.localFor(row.id)?.thumbnails ?? const <Uint8List>[];
    return shots.isEmpty ? null : shots.first;
  }

  Widget _statGrid(_Figures view) {
    final critical = view.severityTotals[Severity.critical] ?? 0;
    return GridView.count(
      crossAxisCount: 2,
      shrinkWrap: true,
      physics: const NeverScrollableScrollPhysics(),
      mainAxisSpacing: 12,
      crossAxisSpacing: 12,
      childAspectRatio: 1.5,
      children: [
        StatCard(
          label: 'Inspections',
          value: '${view.inspections}',
          caption: view.inspectionsCaption,
          accent: Palette.navy,
        ),
        StatCard(
          label: 'Avg score',
          value: view.averageScore.toStringAsFixed(0),
          caption: 'out of 100',
          accent: Palette.brass,
        ),
        StatCard(
          label: 'Compliance rate',
          value: '${view.complianceRate.toStringAsFixed(0)}%',
          caption: 'fully compliant',
          accent: Palette.green,
          valueColor: Palette.green,
        ),
        StatCard(
          label: 'Critical findings',
          value: '$critical',
          caption: 'across all scans',
          accent: Palette.red,
          valueColor: critical > 0 ? Palette.red : Palette.navy,
        ),
      ],
    );
  }

  Widget _card({required Widget child}) => Container(
        width: double.infinity,
        padding: const EdgeInsets.all(18),
        decoration: BoxDecoration(
          color: Palette.card,
          borderRadius: BorderRadius.circular(16),
          border: const Border.fromBorderSide(BorderSide(color: Palette.hairline)),
        ),
        child: child,
      );
}

/// One set of headline numbers, from whichever source is authoritative right now.
///
/// A value type rather than a pile of ternaries at each call site: the dashboard
/// must never show a server average beside a device-local count, and the way to
/// guarantee that is to choose the source once.
class _Figures {
  const _Figures({
    required this.inspections,
    required this.averageScore,
    required this.complianceRate,
    required this.verdictCounts,
    required this.severityTotals,
    required this.fromServer,
    required this.spansEveryone,
  });

  final int inspections;
  final double averageScore;
  final double complianceRate;
  final Map<Verdict, int> verdictCounts;
  final Map<Severity, int> severityTotals;
  final bool fromServer;
  final bool spansEveryone;

  factory _Figures.from({required ScanStore store, required ServerStats? stats}) {
    if (stats == null) {
      return _Figures(
        inspections: store.total,
        averageScore: store.averageScore,
        complianceRate: store.complianceRate,
        verdictCounts: store.verdictCounts,
        severityTotals: store.violationTotals,
        fromServer: false,
        spansEveryone: false,
      );
    }
    return _Figures(
      inspections: stats.totalScans,
      averageScore: stats.averageScore,
      complianceRate: stats.complianceRate,
      // Only the three scored verdicts: a `no_label_detected` read is a mis-aimed
      // photo, and giving it a slice of the compliance donut would misrepresent
      // the caseload.
      verdictCounts: {
        Verdict.compliant: stats.countOf(Verdict.compliant),
        Verdict.needsReview: stats.countOf(Verdict.needsReview),
        Verdict.nonCompliant: stats.countOf(Verdict.nonCompliant),
      },
      severityTotals: {
        Severity.critical: stats.countOfSeverity(Severity.critical),
        Severity.major: stats.countOfSeverity(Severity.major),
        Severity.minor: stats.countOfSeverity(Severity.minor),
      },
      fromServer: true,
      spansEveryone: stats.spansEveryone,
    );
  }

  String get inspectionsCaption {
    if (!fromServer) return 'this session';
    return spansEveryone ? 'filed by everyone' : 'filed by you';
  }

  /// Where these numbers came from. Printed under the grid because a figure whose
  /// scope you cannot see is a figure you cannot quote.
  String get provenance {
    if (!fromServer) {
      return 'Counted on this device · sign in for your filed history';
    }
    return spansEveryone
        ? 'Server totals · every inspection filed on this server'
        : 'Server totals · your filed inspections';
  }
}

/// The enforcement-intelligence panel: which mandatory declaration is breached
/// most often, with the exact provision cited.
///
/// `scans_affected` is the number quoted rather than `occurrences`, because it
/// counts *products in breach* — one label can fail the same declaration twice,
/// and a count of checks would overstate the problem.
class _TopViolations extends StatelessWidget {
  const _TopViolations({required this.rows});
  final List<TopViolation> rows;

  @override
  Widget build(BuildContext context) {
    // The bars are relative to the worst offender, so the shape of the problem is
    // legible at a glance. Computed rather than assumed to be the first row, and
    // floored at 1 so an empty or zeroed corpus cannot divide by zero.
    final worst = rows.isEmpty
        ? 1
        : rows.map((v) => v.scansAffected).fold<int>(1, (a, b) => b > a ? b : a);
    return Container(
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 6),
      decoration: BoxDecoration(
        color: Palette.card,
        borderRadius: BorderRadius.circular(16),
        border: const Border.fromBorderSide(BorderSide(color: Palette.hairline)),
      ),
      child: Column(
        children: [
          for (final v in rows)
            Padding(
              padding: const EdgeInsets.only(bottom: 14),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Expanded(
                        child: Text(
                          v.declarationLabel.isEmpty
                              ? v.declarationId
                              : v.declarationLabel,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: LabelJaanoTheme.display(
                              size: 13.5, weight: FontWeight.w600),
                        ),
                      ),
                      const SizedBox(width: 8),
                      Text('${v.scansAffected}',
                          style: LabelJaanoTheme.readout(
                              size: 14,
                              weight: FontWeight.w700,
                              color: v.severity.color)),
                    ],
                  ),
                  const SizedBox(height: 6),
                  // A fraction of the track rather than a Row of Expandeds: the
                  // top row's remainder is zero, and an Expanded with flex 0 is
                  // laid out unbounded.
                  Container(
                    height: 6,
                    decoration: BoxDecoration(
                      color: Palette.hairline,
                      borderRadius: BorderRadius.circular(4),
                    ),
                    child: FractionallySizedBox(
                      alignment: Alignment.centerLeft,
                      widthFactor:
                          (v.scansAffected / worst).clamp(0.04, 1.0).toDouble(),
                      child: Container(
                        decoration: BoxDecoration(
                          color: v.severity.color,
                          borderRadius: BorderRadius.circular(4),
                        ),
                      ),
                    ),
                  ),
                  const SizedBox(height: 6),
                  Row(
                    children: [
                      Flexible(
                        child: Text(v.legalReference,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: LabelJaanoTheme.readout(
                                size: 11, color: Palette.brassDeep)),
                      ),
                      const Text('  ·  ',
                          style: TextStyle(fontSize: 11, color: Palette.faint)),
                      Text('${v.occurrences} failed checks',
                          style: LabelJaanoTheme.readout(
                              size: 11, color: Palette.faint)),
                    ],
                  ),
                ],
              ),
            ),
        ],
      ),
    );
  }
}

/// Average score per product category — where enforcement attention is worth
/// spending.
class _CategoryTable extends StatelessWidget {
  const _CategoryTable({required this.rows});
  final List<CategoryStat> rows;

  @override
  Widget build(BuildContext context) {
    final sorted = [...rows]
      ..sort((a, b) => a.averageScore.compareTo(b.averageScore));
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
      decoration: BoxDecoration(
        color: Palette.card,
        borderRadius: BorderRadius.circular(16),
        border: const Border.fromBorderSide(BorderSide(color: Palette.hairline)),
      ),
      child: Column(
        children: [
          for (int i = 0; i < sorted.length; i++) ...[
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 11),
              child: Row(
                children: [
                  Expanded(
                    child: Text(sorted[i].label,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: Theme.of(context)
                            .textTheme
                            .bodyLarge
                            ?.copyWith(fontSize: 13.5)),
                  ),
                  Text('${sorted[i].scans} scan${sorted[i].scans == 1 ? '' : 's'}',
                      style: LabelJaanoTheme.readout(size: 11, color: Palette.faint)),
                  const SizedBox(width: 14),
                  Text(sorted[i].averageScore.toStringAsFixed(0),
                      style: LabelJaanoTheme.readout(
                          size: 14,
                          weight: FontWeight.w700,
                          color: _scoreColour(sorted[i].averageScore))),
                ],
              ),
            ),
            if (i != sorted.length - 1) const Divider(height: 1),
          ],
        ],
      ),
    );
  }

  /// The same thresholds the engine uses for its verdict bands, so a category
  /// average is coloured on the scale the officer already reads scores on.
  static Color _scoreColour(double score) {
    if (score >= 85) return Palette.green;
    if (score >= 60) return Palette.amber;
    return Palette.red;
  }
}

class _SignInNudge extends StatelessWidget {
  const _SignInNudge({required this.onTap});
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Palette.brassTint,
      borderRadius: BorderRadius.circular(14),
      child: InkWell(
        borderRadius: BorderRadius.circular(14),
        onTap: onTap,
        child: Container(
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(14),
            border: Border.all(color: Palette.brass.withOpacity(0.4)),
          ),
          child: Row(
            children: [
              const Icon(Icons.login_rounded, color: Palette.brassDeep, size: 20),
              const SizedBox(width: 12),
              Expanded(
                child: Text(
                  'These figures cover this device only. Sign in to keep your '
                  'inspections and see the whole picture.',
                  style: Theme.of(context)
                      .textTheme
                      .bodyMedium
                      ?.copyWith(fontSize: 12.5),
                ),
              ),
              const Icon(Icons.chevron_right_rounded, color: Palette.brassDeep),
            ],
          ),
        ),
      ),
    );
  }
}

class _Hero extends StatelessWidget {
  const _Hero({required this.figures, required this.session});
  final _Figures figures;
  final Session session;

  @override
  Widget build(BuildContext context) {
    final empty = figures.inspections == 0;
    return Container(
      padding: const EdgeInsets.fromLTRB(20, 20, 20, 0),
      decoration: const BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [Palette.navy, Palette.navyDeep],
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SizedBox(height: 6),
          Row(
            children: [
              const Expanded(child: Wordmark(onDark: true, size: 22)),
              _Who(session: session),
            ],
          ),
          const SizedBox(height: 6),
          Text('Legal Metrology compliance · field console',
              style: LabelJaanoTheme.display(
                  size: 12.5, weight: FontWeight.w500, color: Colors.white70)),
          const SizedBox(height: 20),
          Row(
            children: [
              _HeroReadout(
                label: 'Compliance rate',
                value: empty ? '—' : '${figures.complianceRate.toStringAsFixed(0)}%',
              ),
              Container(
                  width: 1,
                  height: 42,
                  margin: const EdgeInsets.symmetric(horizontal: 20),
                  color: Colors.white24),
              _HeroReadout(
                label: 'Avg score',
                value: empty ? '—' : figures.averageScore.toStringAsFixed(0),
              ),
              const Spacer(),
            ],
          ),
          const SizedBox(height: 18),
          const RulerTicks(color: Colors.white24, height: 12),
        ],
      ),
    );
  }
}

/// Who the console is showing, as a small badge in the hero. Presentation only —
/// the server decides what this account may actually see.
///
/// Tappable while signed out, because the badge is where anyone would look for
/// the account, and only when the server actually offers accounts.
class _Who extends StatelessWidget {
  const _Who({required this.session});
  final Session session;

  @override
  Widget build(BuildContext context) {
    final account = session.account;
    final badge = Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
      decoration: BoxDecoration(
        color: Colors.white12,
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: Colors.white24),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(
            account == null
                ? Icons.person_outline_rounded
                : (session.seesEveryScan
                    ? Icons.account_balance_outlined
                    : Icons.verified_user_outlined),
            size: 13,
            color: Colors.white70,
          ),
          const SizedBox(width: 6),
          Text(account == null ? 'Guest' : account.role.shortLabel,
              style: LabelJaanoTheme.eyebrow(color: Colors.white70)),
          if (account == null && session.accountsAvailable) ...[
            const SizedBox(width: 2),
            const Icon(Icons.chevron_right_rounded, size: 14, color: Colors.white54),
          ],
        ],
      ),
    );

    if (account != null || !session.accountsAvailable) return badge;
    return GestureDetector(
      onTap: () => Navigator.of(context).push(
        MaterialPageRoute(builder: (_) => const SignInScreen()),
      ),
      child: badge,
    );
  }
}

class _HeroReadout extends StatelessWidget {
  const _HeroReadout({required this.label, required this.value});
  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(value,
            style: LabelJaanoTheme.readout(
                size: 30, weight: FontWeight.w700, color: Colors.white)),
        const SizedBox(height: 2),
        Text(label.toUpperCase(), style: LabelJaanoTheme.eyebrow(color: Colors.white60)),
      ],
    );
  }
}
