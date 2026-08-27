import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';

import '../core/theme.dart';
import '../models/compliance_report.dart';

/// Verdict mix as a donut. Reads at a glance: how much of today's caseload is
/// clean, borderline, or in breach.
class VerdictDonut extends StatelessWidget {
  const VerdictDonut({super.key, required this.counts});
  final Map<Verdict, int> counts;

  @override
  Widget build(BuildContext context) {
    final total = counts.values.fold<int>(0, (a, b) => a + b);
    final sections = <PieChartSectionData>[];
    counts.forEach((v, c) {
      if (c == 0) return;
      sections.add(PieChartSectionData(
        value: c.toDouble(),
        color: v.color,
        title: '$c',
        radius: 24,
        titleStyle: LabelJaanoTheme.readout(
            size: 12, weight: FontWeight.w700, color: Colors.white),
      ));
    });

    return Row(
      children: [
        SizedBox(
          width: 132,
          height: 132,
          child: Stack(
            alignment: Alignment.center,
            children: [
              if (sections.isEmpty)
                _emptyRing()
              else
                PieChart(PieChartData(
                  sections: sections,
                  sectionsSpace: 3,
                  centerSpaceRadius: 40,
                  startDegreeOffset: -90,
                )),
              Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text('$total',
                      style: LabelJaanoTheme.readout(
                          size: 26, weight: FontWeight.w700)),
                  Text('SCANS', style: LabelJaanoTheme.eyebrow()),
                ],
              ),
            ],
          ),
        ),
        const SizedBox(width: 18),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              for (final v in [Verdict.compliant, Verdict.needsReview, Verdict.nonCompliant])
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: 5),
                  child: Row(
                    children: [
                      Container(
                        width: 10,
                        height: 10,
                        decoration: BoxDecoration(color: v.color, shape: BoxShape.circle),
                      ),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text(v.label,
                            style: Theme.of(context)
                                .textTheme
                                .bodyMedium
                                ?.copyWith(color: Palette.navy, fontSize: 13)),
                      ),
                      Text('${counts[v] ?? 0}',
                          style: LabelJaanoTheme.readout(
                              size: 13, weight: FontWeight.w700, color: v.color)),
                    ],
                  ),
                ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _emptyRing() => Container(
        width: 132,
        height: 132,
        decoration: const BoxDecoration(
          shape: BoxShape.circle,
          border: Border.fromBorderSide(BorderSide(color: Palette.hairline, width: 14)),
        ),
      );
}

/// Open violations by severity as a single proportion bar + legend. Built from
/// layout widgets (not a chart lib) so it always renders crisply and needs no
/// axis chrome for three values.
class SeverityBar extends StatelessWidget {
  const SeverityBar({super.key, required this.totals});
  final Map<Severity, int> totals;

  @override
  Widget build(BuildContext context) {
    final order = [Severity.critical, Severity.major, Severity.minor];
    final total = totals.values.fold<int>(0, (a, b) => a + b);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        ClipRRect(
          borderRadius: BorderRadius.circular(8),
          child: SizedBox(
            height: 16,
            child: total == 0
                ? Container(color: Palette.greenTint)
                : Row(
                    children: [
                      for (final s in order)
                        if ((totals[s] ?? 0) > 0)
                          Expanded(
                            flex: totals[s]!,
                            child: Container(color: s.color),
                          ),
                    ],
                  ),
          ),
        ),
        const SizedBox(height: 12),
        Wrap(
          spacing: 16,
          runSpacing: 8,
          children: [
            for (final s in order)
              Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Container(
                    width: 10,
                    height: 10,
                    decoration: BoxDecoration(color: s.color, shape: BoxShape.circle),
                  ),
                  const SizedBox(width: 6),
                  Text(s.label,
                      style: Theme.of(context)
                          .textTheme
                          .bodyMedium
                          ?.copyWith(fontSize: 12.5)),
                  const SizedBox(width: 6),
                  Text('${totals[s] ?? 0}',
                      style: LabelJaanoTheme.readout(
                          size: 12.5, weight: FontWeight.w700, color: s.color)),
                ],
              ),
          ],
        ),
        if (total == 0) ...[
          const SizedBox(height: 8),
          Text('No open violations recorded yet.',
              style: Theme.of(context).textTheme.bodyMedium?.copyWith(fontSize: 12.5)),
        ],
      ],
    );
  }
}
