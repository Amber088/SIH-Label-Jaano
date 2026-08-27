import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/theme.dart';
import '../models/compliance_report.dart';
import '../models/scan_record.dart';
import '../services/scan_store.dart';
import '../widgets/common.dart';
import '../widgets/result_tiles.dart';
import '../widgets/verdict_banner.dart';

/// The full compliance report for one inspection: the verdict headline, the
/// tally, the captured panels, every violation citing its exact rule, and the
/// complete audited check list (pass / fail / skip). Reached from the queue,
/// the dashboard, or straight after a scan.
class ReportScreen extends StatelessWidget {
  const ReportScreen({super.key, required this.recordId});
  final String recordId;

  @override
  Widget build(BuildContext context) {
    final record = context.watch<ScanStore>().byId(recordId);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Inspection report'),
        actions: [
          if (record != null)
            IconButton(
              tooltip: 'Delete inspection',
              icon: const Icon(Icons.delete_outline_rounded),
              onPressed: () {
                context.read<ScanStore>().remove(recordId);
                Navigator.of(context).pop();
              },
            ),
        ],
      ),
      body: record == null
          ? const EmptyState(
              icon: Icons.search_off_rounded,
              title: 'Inspection not found',
              message: 'It may have been removed from this session.',
            )
          : _ReportBody(record: record),
    );
  }
}

class _ReportBody extends StatelessWidget {
  const _ReportBody({required this.record});
  final ScanRecord record;

  @override
  Widget build(BuildContext context) {
    final r = record.report;

    // A "no label" read ran no checks: the tally, violations, and check list
    // would all be empty (and the green "all clear" would be actively
    // misleading). Show the verdict, a plain explanation, and the photo instead.
    if (r.verdict == Verdict.noLabel) {
      return ListView(
        padding: kPagePadding,
        children: [
          VerdictBanner(report: r),
          const SizedBox(height: 20),
          _NoLabelNote(),
          if (record.thumbnails.isNotEmpty) ...[
            const SizedBox(height: 24),
            const SectionHeader('Scanned image'),
            _Panels(thumbnails: record.thumbnails),
          ],
          if (record.serverMock) ...[
            const SizedBox(height: 20),
            _MockNote(),
          ],
        ],
      );
    }

    final sorted = [...r.violations]
      ..sort((a, b) => a.severity.index.compareTo(b.severity.index));

    return ListView(
      padding: kPagePadding,
      children: [
        VerdictBanner(report: r),
        const SizedBox(height: 20),
        _TallyStrip(summary: r.summary),
        if (record.thumbnails.isNotEmpty) ...[
          const SizedBox(height: 24),
          const SectionHeader('Scanned panels'),
          _Panels(thumbnails: record.thumbnails),
        ],
        const SizedBox(height: 24),
        SectionHeader(
          'Violations',
          trailing: Text('${r.violations.length}',
              style: LabelJaanoTheme.readout(
                  size: 13, weight: FontWeight.w700, color: Palette.red)),
        ),
        if (r.violations.isEmpty)
          _AllClear()
        else
          ...sorted.map((v) => ViolationTile(violation: v)),
        const SizedBox(height: 20),
        SectionHeader(
          'All checks',
          trailing: Text('${r.results.length}',
              style: LabelJaanoTheme.readout(size: 13, weight: FontWeight.w700)),
        ),
        _ChecksList(results: r.results),
        if (r.referenceStandards.isNotEmpty) ...[
          const SizedBox(height: 24),
          SectionHeader(
            'Reference standards',
            trailing: Text('${r.referenceStandards.length}',
                style: LabelJaanoTheme.readout(
                    size: 13, weight: FontWeight.w700, color: Palette.brassDeep)),
          ),
          const _ReferenceIntro(),
          const SizedBox(height: 10),
          _ReferenceList(standards: r.referenceStandards),
        ],
        if (record.serverMock) ...[
          const SizedBox(height: 20),
          _MockNote(),
        ],
      ],
    );
  }
}

class _TallyStrip extends StatelessWidget {
  const _TallyStrip({required this.summary});
  final ReportSummary summary;

  @override
  Widget build(BuildContext context) {
    Widget cell(String label, int value, Color color) => Expanded(
          child: Column(
            children: [
              Text('$value',
                  style: LabelJaanoTheme.readout(
                      size: 22, weight: FontWeight.w700, color: color)),
              const SizedBox(height: 2),
              Text(label.toUpperCase(), style: LabelJaanoTheme.eyebrow()),
            ],
          ),
        );

    return Container(
      padding: const EdgeInsets.symmetric(vertical: 16),
      decoration: BoxDecoration(
        color: Palette.card,
        borderRadius: BorderRadius.circular(16),
        border: const Border.fromBorderSide(BorderSide(color: Palette.hairline)),
      ),
      child: Row(
        children: [
          cell('Passed', summary.passed, Palette.green),
          _sep(),
          cell('Failed', summary.failed, Palette.red),
          _sep(),
          cell('Skipped', summary.skipped, Palette.faint),
          _sep(),
          cell('Checks', summary.checksTotal, Palette.navy),
        ],
      ),
    );
  }

  Widget _sep() => Container(width: 1, height: 34, color: Palette.hairline);
}

class _Panels extends StatelessWidget {
  const _Panels({required this.thumbnails});
  final List<Uint8List> thumbnails;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 150,
      child: ListView.separated(
        scrollDirection: Axis.horizontal,
        itemCount: thumbnails.length,
        separatorBuilder: (_, __) => const SizedBox(width: 12),
        itemBuilder: (context, i) => GestureDetector(
          onTap: () => Navigator.of(context).push(
            MaterialPageRoute(builder: (_) => _FullImage(bytes: thumbnails[i])),
          ),
          child: ClipRRect(
            borderRadius: BorderRadius.circular(12),
            child: Image.memory(thumbnails[i], width: 116, height: 150, fit: BoxFit.cover),
          ),
        ),
      ),
    );
  }
}

class _FullImage extends StatelessWidget {
  const _FullImage({required this.bytes});
  final Uint8List bytes;
  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(
        backgroundColor: Colors.black,
        foregroundColor: Colors.white,
      ),
      body: Center(
        child: InteractiveViewer(
          maxScale: 5,
          child: Image.memory(bytes, fit: BoxFit.contain),
        ),
      ),
    );
  }
}

class _ChecksList extends StatelessWidget {
  const _ChecksList({required this.results});
  final List<CheckResult> results;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14),
      decoration: BoxDecoration(
        color: Palette.card,
        borderRadius: BorderRadius.circular(16),
        border: const Border.fromBorderSide(BorderSide(color: Palette.hairline)),
      ),
      child: Column(
        children: [
          for (int i = 0; i < results.length; i++) ...[
            CheckResultTile(result: results[i]),
            if (i != results.length - 1) const Divider(height: 1),
          ],
        ],
      ),
    );
  }
}

/// A short caption that frames the Tier-2 block: these provisions apply to the
/// product but a photo can't judge them, so they are shown for lab follow-up and
/// deliberately excluded from the score and verdict.
class _ReferenceIntro extends StatelessWidget {
  const _ReferenceIntro();

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: 4, bottom: 2),
      child: Text(
        'These provisions apply to this product but cannot be verified from a '
        'photograph (composition, additive limits, lab-only safety parameters). '
        'They are listed for laboratory follow-up and do not affect the score or '
        'verdict.',
        style: Theme.of(context)
            .textTheme
            .bodyMedium
            ?.copyWith(fontSize: 12.5, color: Palette.muted),
      ),
    );
  }
}

/// The Tier-2 list itself. Styled with the brass authority accent (not the
/// green/red scoring signals) so it reads as reference context, not a verdict.
class _ReferenceList extends StatelessWidget {
  const _ReferenceList({required this.standards});
  final List<ReferenceStandard> standards;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        for (final s in standards)
          Container(
            margin: const EdgeInsets.only(bottom: 10),
            padding: const EdgeInsets.all(14),
            decoration: BoxDecoration(
              color: Palette.brassTint,
              borderRadius: BorderRadius.circular(14),
              border: Border.all(color: Palette.brass.withOpacity(0.35)),
            ),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Icon(Icons.biotech_outlined, size: 20, color: Palette.brassDeep),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(s.label,
                          style: Theme.of(context)
                              .textTheme
                              .titleMedium
                              ?.copyWith(fontSize: 14.5, fontWeight: FontWeight.w700)),
                      const SizedBox(height: 4),
                      Row(
                        children: [
                          Flexible(
                            child: Text(s.legalReference,
                                style: LabelJaanoTheme.readout(
                                    size: 12, color: Palette.brassDeep)),
                          ),
                          if (s.authority.isNotEmpty) ...[
                            const Text('  ·  ',
                                style: TextStyle(fontSize: 12, color: Palette.faint)),
                            Flexible(
                              child: Text(s.authority,
                                  style: Theme.of(context)
                                      .textTheme
                                      .bodySmall
                                      ?.copyWith(fontSize: 12, color: Palette.muted)),
                            ),
                          ],
                        ],
                      ),
                      if (s.note.isNotEmpty) ...[
                        const SizedBox(height: 6),
                        Text(s.note,
                            style: Theme.of(context)
                                .textTheme
                                .bodyMedium
                                ?.copyWith(fontSize: 13, height: 1.35)),
                      ],
                      const SizedBox(height: 8),
                      Container(
                        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                        decoration: BoxDecoration(
                          color: Palette.card,
                          borderRadius: BorderRadius.circular(6),
                          border: Border.all(color: Palette.hairline),
                        ),
                        child: Text('VERIFY IN LAB',
                            style: LabelJaanoTheme.eyebrow(color: Palette.brassDeep)),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
      ],
    );
  }
}

class _AllClear extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Palette.greenTint,
        borderRadius: BorderRadius.circular(14),
      ),
      child: Row(
        children: [
          const Icon(Icons.verified_rounded, color: Palette.green),
          const SizedBox(width: 12),
          Expanded(
            child: Text('No violations. Every mandatory declaration the engine '
                'evaluated passed.',
                style: Theme.of(context).textTheme.bodyLarge?.copyWith(fontSize: 14)),
          ),
        ],
      ),
    );
  }
}

/// Shown when the backend returns the `no_label_detected` verdict — the image
/// carried no readable packaged-commodity label (a book cover, a face, scenery).
/// Deliberately neutral: this is not a compliance failure, just "wrong photo".
class _NoLabelNote extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Palette.paper,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: Palette.hairline),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Icon(Icons.image_not_supported_outlined, color: Palette.muted),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('No product label found',
                    style: Theme.of(context)
                        .textTheme
                        .titleMedium
                        ?.copyWith(fontWeight: FontWeight.w700)),
                const SizedBox(height: 6),
                Text(
                  'Nothing on this image reads like a packaged-commodity label — '
                  'no net quantity, MRP, manufacturer, or date. Point the camera '
                  'at the printed declaration panel and scan again.',
                  style: Theme.of(context).textTheme.bodyMedium?.copyWith(fontSize: 13.5),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _MockNote extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: Palette.paper,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: Palette.hairline),
      ),
      child: Row(
        children: [
          const Icon(Icons.science_outlined, size: 18, color: Palette.faint),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              'Produced by the backend’s offline mock pipeline — real OCR + Gemini '
              'were not used for this read.',
              style: Theme.of(context).textTheme.bodyMedium?.copyWith(fontSize: 12.5),
            ),
          ),
        ],
      ),
    );
  }
}
