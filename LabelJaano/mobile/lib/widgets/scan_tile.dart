import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import '../core/theme.dart';
import '../models/saved_scan.dart';
import 'common.dart';

/// One inspection row — thumbnail, verdict, score, and when. Shared by the
/// dashboard's recent-activity list and the officer queue.
///
/// Takes a [SavedScan] rather than a local record so a row filed on another
/// device renders identically to one scanned here. [thumbnail] is supplied
/// separately, and only this device has one: the API never stores the photo.
class ScanTile extends StatelessWidget {
  const ScanTile({
    super.key,
    required this.row,
    this.thumbnail,
    this.ownerLabel,
    this.onTap,
  });

  final SavedScan row;

  /// First captured panel, when this device took the scan.
  final Uint8List? thumbnail;

  /// Who filed it — shown only in an officer's queue, which spans other
  /// inspectors' work. Null hides the line entirely rather than printing an
  /// empty row, which is what a consumer should see.
  final String? ownerLabel;

  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final at = row.capturedAt;
    final time = at == null ? '—' : DateFormat('d MMM, h:mm a').format(at);
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
              _Thumb(row: row, thumbnail: thumbnail),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Expanded(
                          child: Text(
                            row.title,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: LabelJaanoTheme.display(
                                size: 14.5, weight: FontWeight.w600),
                          ),
                        ),
                        if (!row.filed) ...[
                          const SizedBox(width: 6),
                          const _Tag('LOCAL'),
                        ],
                        if (row.mock) ...[
                          const SizedBox(width: 6),
                          const _Tag('MOCK'),
                        ],
                      ],
                    ),
                    const SizedBox(height: 6),
                    Row(
                      children: [
                        VerdictPill(row.verdict, dense: true),
                        const SizedBox(width: 8),
                        Text('${row.violationsTotal} findings',
                            style: Theme.of(context)
                                .textTheme
                                .bodyMedium
                                ?.copyWith(fontSize: 12)),
                      ],
                    ),
                    const SizedBox(height: 6),
                    Text(
                      _footer(time),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: LabelJaanoTheme.readout(size: 11, color: Palette.faint),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 8),
              Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Text(row.score.toStringAsFixed(0),
                      style: LabelJaanoTheme.readout(
                          size: 22, weight: FontWeight.w700, color: row.verdict.color)),
                  Text('/100',
                      style: LabelJaanoTheme.readout(size: 10, color: Palette.faint)),
                ],
              ),
              const Icon(Icons.chevron_right_rounded, color: Palette.faint),
            ],
          ),
        ),
      ),
    );
  }

  /// Time, then place, then who — in the order an officer scanning the list
  /// needs them, and each part only when it exists.
  String _footer(String time) {
    final parts = <String>[time];
    final place = (row.location ?? '').trim();
    if (place.isNotEmpty) parts.add(place);
    final owner = (ownerLabel ?? '').trim();
    if (owner.isNotEmpty) parts.add(owner);
    return parts.join('  ·  ');
  }
}

class _Thumb extends StatelessWidget {
  const _Thumb({required this.row, this.thumbnail});
  final SavedScan row;
  final Uint8List? thumbnail;

  @override
  Widget build(BuildContext context) {
    final image = thumbnail;
    return ClipRRect(
      borderRadius: BorderRadius.circular(10),
      child: SizedBox(
        width: 52,
        height: 52,
        child: image != null
            ? Image.memory(image, fit: BoxFit.cover)
            : Container(
                color: row.verdict.tint,
                child: Icon(
                    row.source == 'image'
                        ? Icons.photo_camera_back_outlined
                        : Icons.inventory_2_outlined,
                    color: row.verdict.color,
                    size: 22),
              ),
      ),
    );
  }
}

class _Tag extends StatelessWidget {
  const _Tag(this.text);
  final String text;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: Palette.paper,
        borderRadius: BorderRadius.circular(5),
        border: Border.all(color: Palette.hairline),
      ),
      child: Text(text, style: LabelJaanoTheme.eyebrow(color: Palette.faint)),
    );
  }
}
