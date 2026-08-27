import 'package:flutter/material.dart';

import '../core/theme.dart';
import '../models/compliance_report.dart';
import 'common.dart';
import 'score_gauge.dart';

/// The report headline: the verdict, its plain-language meaning, the score
/// gauge, and the applied rule packs. Colour-washed by verdict so an officer
/// reads the outcome before a single word.
class VerdictBanner extends StatelessWidget {
  const VerdictBanner({super.key, required this.report});
  final ComplianceReport report;

  @override
  Widget build(BuildContext context) {
    final v = report.verdict;
    // A "no label" read evaluated nothing, so a 0/100 gauge would misrepresent it
    // as a total failure. Drop the gauge and just state the verdict.
    final showScore = v != Verdict.noLabel;
    return Container(
      decoration: BoxDecoration(
        color: v.tint,
        borderRadius: BorderRadius.circular(18),
        border: Border.all(color: v.color.withOpacity(0.35)),
      ),
      // IntrinsicHeight gives the Row a bounded height so the stretched colour
      // spine can size to the content. Without it, CrossAxisAlignment.stretch
      // inside the report's vertical ListView resolves against an unbounded
      // height and the whole body fails layout (renders blank).
      child: IntrinsicHeight(
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            // Verdict-coloured spine
            Container(
              width: 6,
              decoration: BoxDecoration(
                color: v.color,
                borderRadius:
                    const BorderRadius.horizontal(left: Radius.circular(18)),
              ),
            ),
            Expanded(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(18, 18, 14, 18),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text('VERDICT',
                                  style:
                                      LabelJaanoTheme.eyebrow(color: v.color)),
                              const SizedBox(height: 4),
                              Text(
                                v.label,
                                style: LabelJaanoTheme.display(
                                    size: 26,
                                    weight: FontWeight.w700,
                                    color: v.color),
                              ),
                              const SizedBox(height: 6),
                              Text(v.gloss,
                                  style: Theme.of(context).textTheme.bodyMedium),
                            ],
                          ),
                        ),
                        const SizedBox(width: 8),
                        if (showScore)
                          ScoreGauge(score: report.score, verdict: v, size: 128),
                      ],
                    ),
                    const SizedBox(height: 16),
                    Wrap(
                      spacing: 8,
                      runSpacing: 8,
                      children: [
                        ReadoutChip(report.categoryLabel, color: Palette.navy),
                        for (final p in report.packsApplied)
                          ReadoutChip(p,
                              color: Palette.brassDeep, bg: Palette.brassTint),
                      ],
                    ),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
