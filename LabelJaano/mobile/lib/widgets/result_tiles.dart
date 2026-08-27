import 'package:flutter/material.dart';

import '../core/theme.dart';
import '../models/compliance_report.dart';
import 'common.dart';

/// A single violation, laid out like an inspection finding: severity, what
/// failed, the exact legal citation, and the engine's explanation.
class ViolationTile extends StatelessWidget {
  const ViolationTile({super.key, required this.violation});
  final Violation violation;

  @override
  Widget build(BuildContext context) {
    final sev = violation.severity;
    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: Palette.card,
        borderRadius: BorderRadius.circular(14),
        border: const Border.fromBorderSide(BorderSide(color: Palette.hairline)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Container(
                margin: const EdgeInsets.only(top: 2),
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                decoration: BoxDecoration(
                  color: sev.color,
                  borderRadius: BorderRadius.circular(6),
                ),
                child: Text(sev.label.toUpperCase(),
                    style: LabelJaanoTheme.eyebrow(color: Colors.white)),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Text(violation.declarationLabel,
                    style: LabelJaanoTheme.display(size: 15, weight: FontWeight.w600)),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Text(violation.message,
              style: Theme.of(context).textTheme.bodyLarge?.copyWith(fontSize: 14)),
          if (violation.detail != null && violation.detail!.isNotEmpty) ...[
            const SizedBox(height: 4),
            Text(violation.detail!,
                style: Theme.of(context)
                    .textTheme
                    .bodyMedium
                    ?.copyWith(fontSize: 12.5, color: Palette.faint)),
          ],
          const SizedBox(height: 10),
          Row(
            children: [
              const Icon(Icons.gavel_rounded, size: 14, color: Palette.brassDeep),
              const SizedBox(width: 6),
              Flexible(child: ReadoutChip(violation.legalReference, color: Palette.brassDeep, bg: Palette.brassTint)),
              const SizedBox(width: 6),
              ReadoutChip(violation.checkType, color: Palette.muted),
            ],
          ),
        ],
      ),
    );
  }
}

/// A single audited check — pass / fail / skip — with its citation. Together
/// these show the *full* picture the engine evaluated, not just the failures.
class CheckResultTile extends StatelessWidget {
  const CheckResultTile({super.key, required this.result});
  final CheckResult result;

  IconData get _icon => switch (result.outcome) {
        Outcome.pass => Icons.check_circle_rounded,
        Outcome.fail => Icons.cancel_rounded,
        Outcome.skip => Icons.remove_circle_outline_rounded,
        Outcome.unknown => Icons.help_outline_rounded,
      };

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 10),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(_icon, size: 20, color: result.outcome.color),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Expanded(
                      child: Text(result.declarationLabel,
                          style: LabelJaanoTheme.display(
                              size: 14, weight: FontWeight.w600)),
                    ),
                    ReadoutChip(result.checkType, color: Palette.muted),
                  ],
                ),
                if (result.detail != null && result.detail!.isNotEmpty) ...[
                  const SizedBox(height: 3),
                  Text(result.detail!,
                      style: Theme.of(context)
                          .textTheme
                          .bodyMedium
                          ?.copyWith(fontSize: 12.5)),
                ],
                const SizedBox(height: 5),
                Text(result.legalReference,
                    style: LabelJaanoTheme.readout(size: 11, color: Palette.faint)),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
