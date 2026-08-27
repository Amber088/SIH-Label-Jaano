import 'package:flutter/material.dart';

import '../core/theme.dart';

/// A dashboard metric tile: eyebrow label, a big monospace readout value, and
/// an optional caption. The accent bar keeps the grid visually rhythmic.
class StatCard extends StatelessWidget {
  const StatCard({
    super.key,
    required this.label,
    required this.value,
    this.caption,
    this.accent = Palette.brass,
    this.valueColor = Palette.navy,
    this.icon,
  });

  final String label;
  final String value;
  final String? caption;
  final Color accent;
  final Color valueColor;
  final IconData? icon;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Palette.card,
        borderRadius: BorderRadius.circular(16),
        border: const Border.fromBorderSide(BorderSide(color: Palette.hairline)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Row(
            children: [
              Container(width: 18, height: 3, color: accent),
              const SizedBox(width: 8),
              Expanded(
                child: Text(label.toUpperCase(),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: LabelJaanoTheme.eyebrow(color: Palette.muted)),
              ),
              if (icon != null) Icon(icon, size: 16, color: Palette.faint),
            ],
          ),
          const SizedBox(height: 14),
          Text(value,
              style: LabelJaanoTheme.readout(
                  size: 30, weight: FontWeight.w700, color: valueColor)),
          if (caption != null) ...[
            const SizedBox(height: 4),
            Text(caption!,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: Theme.of(context).textTheme.bodyMedium?.copyWith(fontSize: 12.5)),
          ],
        ],
      ),
    );
  }
}
