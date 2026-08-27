import 'package:flutter/material.dart';

import '../core/theme.dart';
import '../models/compliance_report.dart';

/// A small ALL-CAPS section header with the ruler-tick eyebrow style.
class SectionHeader extends StatelessWidget {
  const SectionHeader(this.title, {super.key, this.trailing});
  final String title;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.center,
        children: [
          Text(title.toUpperCase(), style: LabelJaanoTheme.eyebrow(color: Palette.muted)),
          const SizedBox(width: 10),
          const Expanded(child: Divider(height: 1)),
          if (trailing != null) ...[const SizedBox(width: 10), trailing!],
        ],
      ),
    );
  }
}

/// The verdict as a compact pill — coloured dot + label. Used in lists and bars.
class VerdictPill extends StatelessWidget {
  const VerdictPill(this.verdict, {super.key, this.dense = false});
  final Verdict verdict;
  final bool dense;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: EdgeInsets.symmetric(horizontal: dense ? 8 : 10, vertical: dense ? 4 : 6),
      decoration: BoxDecoration(
        color: verdict.tint,
        borderRadius: BorderRadius.circular(8),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            width: 8,
            height: 8,
            decoration: BoxDecoration(color: verdict.color, shape: BoxShape.circle),
          ),
          const SizedBox(width: 6),
          Text(
            verdict.label,
            style: LabelJaanoTheme.display(
              size: dense ? 11.5 : 12.5,
              weight: FontWeight.w600,
              color: verdict.color,
            ),
          ),
        ],
      ),
    );
  }
}

/// A neutral labelled chip (e.g. "Rule 6(1)(c)", "font_height"). Monospace so
/// codes and references read like instrument labels.
class ReadoutChip extends StatelessWidget {
  const ReadoutChip(this.text, {super.key, this.color = Palette.navy, this.bg});
  final String text;
  final Color color;
  final Color? bg;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: BoxDecoration(
        color: bg ?? Palette.paper,
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: Palette.hairline),
      ),
      child: Text(text, style: LabelJaanoTheme.readout(size: 11.5, color: color)),
    );
  }
}

/// A friendly empty state — a prompt to act, not a dead end.
class EmptyState extends StatelessWidget {
  const EmptyState({
    super.key,
    required this.icon,
    required this.title,
    required this.message,
    this.action,
  });

  final IconData icon;
  final String title;
  final String message;
  final Widget? action;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              width: 64,
              height: 64,
              decoration: BoxDecoration(
                color: Palette.brassTint,
                borderRadius: BorderRadius.circular(18),
              ),
              child: Icon(icon, color: Palette.brassDeep, size: 30),
            ),
            const SizedBox(height: 18),
            Text(title,
                textAlign: TextAlign.center,
                style: LabelJaanoTheme.display(size: 18, weight: FontWeight.w600)),
            const SizedBox(height: 8),
            Text(message,
                textAlign: TextAlign.center,
                style: Theme.of(context).textTheme.bodyMedium),
            if (action != null) ...[const SizedBox(height: 20), action!],
          ],
        ),
      ),
    );
  }
}

/// Standard page padding used across screens.
const kPagePadding = EdgeInsets.fromLTRB(20, 16, 20, 32);
