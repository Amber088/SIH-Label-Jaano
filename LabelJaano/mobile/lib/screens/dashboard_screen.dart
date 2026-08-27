import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/theme.dart';
import '../models/compliance_report.dart';
import '../services/scan_store.dart';
import '../widgets/brand.dart';
import '../widgets/charts.dart';
import '../widgets/common.dart';
import '../widgets/scan_tile.dart';
import '../widgets/stat_card.dart';
import 'report_screen.dart';

/// The supervisor's-eye view: today's caseload at a glance — how many packages
/// were inspected, the average compliance score, the verdict mix, open
/// violations by severity, and a tap-through recent-activity feed. Everything
/// is a live projection of [ScanStore], so each scan updates it instantly.
class DashboardScreen extends StatelessWidget {
  const DashboardScreen({super.key, required this.onScan, required this.onSeeQueue});

  final VoidCallback onScan;
  final VoidCallback onSeeQueue;

  @override
  Widget build(BuildContext context) {
    final store = context.watch<ScanStore>();

    return CustomScrollView(
      slivers: [
        SliverToBoxAdapter(child: _Hero(store: store)),
        if (store.isEmpty)
          SliverFillRemaining(
            hasScrollBody: false,
            child: EmptyState(
              icon: Icons.center_focus_strong_rounded,
              title: 'No inspections yet',
              message:
                  'Scan a package label to get a verdict in seconds. Your results '
                  'build the dashboard and the officer queue as you go.',
              action: FilledButton.icon(
                onPressed: onScan,
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
                _statGrid(store),
                const SizedBox(height: 24),
                const SectionHeader('Verdict mix'),
                _card(child: VerdictDonut(counts: store.verdictCounts)),
                const SizedBox(height: 24),
                const SectionHeader('Open violations by severity'),
                _card(child: SeverityBar(totals: store.violationTotals)),
                const SizedBox(height: 24),
                SectionHeader(
                  'Recent activity',
                  trailing: GestureDetector(
                    onTap: onSeeQueue,
                    child: Text('View queue',
                        style: LabelJaanoTheme.display(
                            size: 12.5, weight: FontWeight.w600, color: Palette.brassDeep)),
                  ),
                ),
                ...store.recent.take(5).map(
                      (r) => Padding(
                        padding: const EdgeInsets.only(bottom: 10),
                        child: ScanTile(
                          record: r,
                          onTap: () => Navigator.of(context).push(
                            MaterialPageRoute(builder: (_) => ReportScreen(recordId: r.id)),
                          ),
                        ),
                      ),
                    ),
              ]),
            ),
          ),
      ],
    );
  }

  Widget _statGrid(ScanStore store) {
    final v = store.violationTotals;
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
          value: '${store.total}',
          caption: 'this session',
          accent: Palette.navy,
        ),
        StatCard(
          label: 'Avg score',
          value: store.averageScore.toStringAsFixed(0),
          caption: 'out of 100',
          accent: Palette.brass,
        ),
        StatCard(
          label: 'Compliance rate',
          value: '${store.complianceRate.toStringAsFixed(0)}%',
          caption: 'fully compliant',
          accent: Palette.green,
          valueColor: Palette.green,
        ),
        StatCard(
          label: 'Critical findings',
          value: '${v[Severity.critical] ?? 0}',
          caption: 'across all scans',
          accent: Palette.red,
          valueColor: (v[Severity.critical] ?? 0) > 0 ? Palette.red : Palette.navy,
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

class _Hero extends StatelessWidget {
  const _Hero({required this.store});
  final ScanStore store;

  @override
  Widget build(BuildContext context) {
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
          const Wordmark(onDark: true, size: 22),
          const SizedBox(height: 6),
          Text('Legal Metrology compliance · field console',
              style: LabelJaanoTheme.display(
                  size: 12.5, weight: FontWeight.w500, color: Colors.white70)),
          const SizedBox(height: 20),
          Row(
            children: [
              _HeroReadout(
                label: 'Compliance rate',
                value: store.isEmpty ? '—' : '${store.complianceRate.toStringAsFixed(0)}%',
              ),
              Container(
                  width: 1,
                  height: 42,
                  margin: const EdgeInsets.symmetric(horizontal: 20),
                  color: Colors.white24),
              _HeroReadout(
                label: 'Avg score',
                value: store.isEmpty ? '—' : store.averageScore.toStringAsFixed(0),
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
