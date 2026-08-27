import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import '../core/theme.dart';
import '../models/scan_record.dart';
import 'common.dart';

/// One inspection row — thumbnail, verdict, score, and when. Shared by the
/// dashboard's recent-activity list and the officer queue.
class ScanTile extends StatelessWidget {
  const ScanTile({super.key, required this.record, this.onTap});
  final ScanRecord record;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final r = record.report;
    final time = DateFormat('d MMM, h:mm a').format(record.capturedAt);
    return Material(
      color: Palette.card,
      borderRadius: BorderRadius.circular(14),
      child: InkWell(
        borderRadius: BorderRadius.circular(14),
        onTap: onTap,
        child: Container(
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(14),
            border: const Border.fromBorderSide(BorderSide(color: Palette.hairline)),
          ),
          child: Row(
            children: [
              _Thumb(record: record),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Expanded(
                          child: Text(
                            record.note?.isNotEmpty == true
                                ? record.note!
                                : r.categoryLabel,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: LabelJaanoTheme.display(
                                size: 14.5, weight: FontWeight.w600),
                          ),
                        ),
                        if (record.serverMock) ...[
                          const SizedBox(width: 6),
                          const _MockTag(),
                        ],
                      ],
                    ),
                    const SizedBox(height: 6),
                    Row(
                      children: [
                        VerdictPill(record.verdict, dense: true),
                        const SizedBox(width: 8),
                        Text('${r.summary.violationsTotal} findings',
                            style: Theme.of(context)
                                .textTheme
                                .bodyMedium
                                ?.copyWith(fontSize: 12)),
                      ],
                    ),
                    const SizedBox(height: 6),
                    Text(time,
                        style: LabelJaanoTheme.readout(size: 11, color: Palette.faint)),
                  ],
                ),
              ),
              const SizedBox(width: 8),
              Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Text(record.score.toStringAsFixed(0),
                      style: LabelJaanoTheme.readout(
                          size: 22, weight: FontWeight.w700, color: record.verdict.color)),
                  Text('/100', style: LabelJaanoTheme.readout(size: 10, color: Palette.faint)),
                ],
              ),
              const Icon(Icons.chevron_right_rounded, color: Palette.faint),
            ],
          ),
        ),
      ),
    );
  }
}

class _Thumb extends StatelessWidget {
  const _Thumb({required this.record});
  final ScanRecord record;

  @override
  Widget build(BuildContext context) {
    final hasImage = record.thumbnails.isNotEmpty;
    return ClipRRect(
      borderRadius: BorderRadius.circular(10),
      child: SizedBox(
        width: 52,
        height: 52,
        child: hasImage
            ? Image.memory(record.thumbnails.first, fit: BoxFit.cover)
            : Container(
                color: record.verdict.tint,
                child: Icon(Icons.inventory_2_outlined,
                    color: record.verdict.color, size: 22),
              ),
      ),
    );
  }
}

class _MockTag extends StatelessWidget {
  const _MockTag();
  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: Palette.paper,
        borderRadius: BorderRadius.circular(5),
        border: Border.all(color: Palette.hairline),
      ),
      child: Text('MOCK', style: LabelJaanoTheme.eyebrow(color: Palette.faint)),
    );
  }
}
