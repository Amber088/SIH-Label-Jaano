import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/theme.dart';
import '../models/compliance_report.dart';
import '../models/scan_record.dart';
import '../services/scan_store.dart';
import '../widgets/common.dart';
import '../widgets/scan_tile.dart';
import 'report_screen.dart';

/// The officer queue: every inspection this session, newest first, filterable
/// by verdict. This is where a supervisor triages what needs follow-up.
class QueueScreen extends StatefulWidget {
  const QueueScreen({super.key});

  @override
  State<QueueScreen> createState() => _QueueScreenState();
}

class _QueueScreenState extends State<QueueScreen> {
  Verdict? _filter; // null = all

  @override
  Widget build(BuildContext context) {
    final store = context.watch<ScanStore>();
    final all = store.recent;
    final items = _filter == null
        ? all
        : all.where((r) => r.verdict == _filter).toList();

    return Column(
      children: [
        _Header(store: store),
        _Filters(
          selected: _filter,
          counts: store.verdictCounts,
          total: store.total,
          onSelect: (v) => setState(() => _filter = v),
        ),
        const SizedBox(height: 4),
        Expanded(
          child: store.isEmpty
              ? const EmptyState(
                  icon: Icons.inbox_rounded,
                  title: 'The queue is empty',
                  message:
                      'Inspections you run appear here for triage and follow-up.',
                )
              : items.isEmpty
                  ? EmptyState(
                      icon: Icons.filter_alt_off_rounded,
                      title: 'Nothing matches this filter',
                      message: 'No inspections with the “${_filter!.label}” verdict yet.',
                      action: OutlinedButton(
                        onPressed: () => setState(() => _filter = null),
                        child: const Text('Show all'),
                      ),
                    )
                  : ListView.separated(
                      padding: kPagePadding,
                      itemCount: items.length,
                      separatorBuilder: (_, __) => const SizedBox(height: 10),
                      itemBuilder: (context, i) => ScanTile(
                        record: items[i],
                        onTap: () => Navigator.of(context).push(
                          MaterialPageRoute(
                              builder: (_) => ReportScreen(recordId: items[i].id)),
                        ),
                      ),
                    ),
        ),
      ],
    );
  }
}

class _Header extends StatelessWidget {
  const _Header({required this.store});
  final ScanStore store;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 12, 20, 8),
      child: Row(
        children: [
          Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('OFFICER QUEUE', style: LabelJaanoTheme.eyebrow(color: Palette.muted)),
              const SizedBox(height: 2),
              Text('Inspections',
                  style: LabelJaanoTheme.display(size: 22, weight: FontWeight.w700)),
            ],
          ),
          const Spacer(),
          if (!store.isEmpty)
            Text('${store.total} total',
                style: LabelJaanoTheme.readout(size: 13, color: Palette.muted)),
        ],
      ),
    );
  }
}

class _Filters extends StatelessWidget {
  const _Filters({
    required this.selected,
    required this.counts,
    required this.total,
    required this.onSelect,
  });

  final Verdict? selected;
  final Map<Verdict, int> counts;
  final int total;
  final ValueChanged<Verdict?> onSelect;

  @override
  Widget build(BuildContext context) {
    Widget chip(String label, Verdict? v, int count, Color color) {
      final isSel = selected == v;
      return Padding(
        padding: const EdgeInsets.only(right: 8),
        child: GestureDetector(
          onTap: () => onSelect(v),
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
            decoration: BoxDecoration(
              color: isSel ? Palette.navy : Palette.card,
              borderRadius: BorderRadius.circular(10),
              border: Border.all(color: isSel ? Palette.navy : Palette.hairline),
            ),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                if (v != null)
                  Container(
                    width: 8,
                    height: 8,
                    margin: const EdgeInsets.only(right: 6),
                    decoration: BoxDecoration(color: color, shape: BoxShape.circle),
                  ),
                Text(label,
                    style: LabelJaanoTheme.display(
                        size: 13,
                        weight: FontWeight.w600,
                        color: isSel ? Colors.white : Palette.navy)),
                const SizedBox(width: 6),
                Text('$count',
                    style: LabelJaanoTheme.readout(
                        size: 12,
                        weight: FontWeight.w700,
                        color: isSel ? Colors.white70 : Palette.faint)),
              ],
            ),
          ),
        ),
      );
    }

    return SizedBox(
      height: 44,
      child: ListView(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: 20),
        children: [
          chip('All', null, total, Palette.navy),
          chip('Compliant', Verdict.compliant, counts[Verdict.compliant] ?? 0, Palette.green),
          chip('Needs review', Verdict.needsReview, counts[Verdict.needsReview] ?? 0,
              Palette.amber),
          chip('Non-compliant', Verdict.nonCompliant, counts[Verdict.nonCompliant] ?? 0,
              Palette.red),
        ],
      ),
    );
  }
}
