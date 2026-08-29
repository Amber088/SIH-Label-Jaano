import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import '../core/theme.dart';
import '../models/compliance_report.dart';
import '../models/saved_scan.dart';
import '../services/api_client.dart';
import '../services/scan_store.dart';
import '../services/session.dart';
import '../services/settings.dart';
import '../widgets/common.dart';
import '../widgets/result_tiles.dart';
import '../widgets/verdict_banner.dart';

/// The full compliance report for one inspection: the verdict headline, the
/// tally, the captured panels, every violation citing its exact rule, and the
/// complete audited check list (pass / fail / skip). Reached from the queue,
/// the dashboard, or straight after a scan.
///
/// The report body can arrive from either of two places, and this screen has to
/// cope with both. A scan taken on this device carries its report (and its
/// photos) in memory. A row filed on *another* device arrives from the history
/// list without a body — the list endpoint omits it deliberately — so it is
/// fetched on entry. Hence a StatefulWidget: opening an inspection can involve a
/// request.
class ReportScreen extends StatefulWidget {
  const ReportScreen({super.key, required this.recordId});
  final String recordId;

  @override
  State<ReportScreen> createState() => _ReportScreenState();
}

class _ReportScreenState extends State<ReportScreen> {
  bool _loading = false;
  String? _loadError;

  /// A share or delete is in flight. Blocks both buttons, because either one
  /// firing twice is a mess: two links to revoke, or a delete racing a fetch.
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _hydrate());
  }

  Future<void> _hydrate() async {
    final store = context.read<ScanStore>();
    final row = store.byId(widget.recordId);
    // Nothing to do for a scan taken here: its report never left memory.
    if (row == null || row.hasReport) return;
    setState(() => _loading = true);
    try {
      await store.loadDetail(widget.recordId);
    } on ApiException catch (e) {
      if (mounted) setState(() => _loadError = e.message);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _delete(SavedScan row) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        backgroundColor: Palette.card,
        title: Text('Delete this inspection?',
            style: LabelJaanoTheme.display(size: 18)),
        content: Text(
          row.filed
              ? 'This removes the inspection from the server as well as this '
                  'device. It cannot be undone.'
              : 'This scan was never filed, so it only exists on this device.',
          style: Theme.of(context).textTheme.bodyLarge,
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('Keep')),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Delete', style: TextStyle(color: Palette.red)),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;

    setState(() => _busy = true);
    try {
      await context.read<ScanStore>().deleteScan(widget.recordId);
      if (mounted) Navigator.of(context).pop();
    } on ApiException catch (e) {
      if (mounted) {
        setState(() => _busy = false);
        _toast(e.message);
      }
    }
  }

  /// Mint a short-lived link to this report and offer it for copying.
  ///
  /// This is how an inspection gets printed or emailed: a phone cannot print, and
  /// a browser address bar cannot send an Authorization header. The ticket in the
  /// link opens this one report for a few minutes and is refused everywhere a
  /// session token is expected, so forwarding it does not hand over the account.
  Future<void> _share() async {
    final store = context.read<ScanStore>();
    final base = context.read<Settings>().baseUrl;
    setState(() => _busy = true);
    try {
      final link = await store.shareReport(widget.recordId);
      if (!mounted) return;
      final url = link.urlFrom(ApiClient.normaliseBase(base));
      await showModalBottomSheet<void>(
        context: context,
        backgroundColor: Palette.card,
        isScrollControlled: true,
        shape: const RoundedRectangleBorder(
            borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
        builder: (_) => _ShareSheet(url: url, validFor: link.validFor),
      );
    } on ApiException catch (e) {
      if (mounted) _toast(e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _toast(String msg) =>
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));

  @override
  Widget build(BuildContext context) {
    final store = context.watch<ScanStore>();
    final session = context.watch<Session>();
    final row = store.byId(widget.recordId);

    // Only this device has the photos — the API never stores an image — so they
    // come from the local record rather than the row.
    final local = store.localFor(widget.recordId);
    final report = row?.report ?? local?.report;
    final canShare = row != null && row.filed && session.isSignedIn;

    return Scaffold(
      appBar: AppBar(
        title: const Text('Inspection report'),
        actions: [
          if (canShare)
            IconButton(
              tooltip: 'Share a printable link',
              icon: const Icon(Icons.ios_share_rounded),
              onPressed: _busy ? null : _share,
            ),
          if (row != null)
            IconButton(
              tooltip: 'Delete inspection',
              icon: const Icon(Icons.delete_outline_rounded),
              onPressed: _busy ? null : () => _delete(row),
            ),
        ],
      ),
      body: _body(row, report, local?.thumbnails ?? const [], session),
    );
  }

  Widget _body(SavedScan? row, ComplianceReport? report,
      List<Uint8List> thumbnails, Session session) {
    if (row == null) {
      return const EmptyState(
        icon: Icons.search_off_rounded,
        title: 'Inspection not found',
        message: 'It may have been deleted, or belong to another account.',
      );
    }
    if (report == null) {
      if (_loading) {
        return const Center(child: CircularProgressIndicator());
      }
      return EmptyState(
        icon: Icons.cloud_download_outlined,
        title: 'Report not loaded',
        message: _loadError ??
            (session.isSignedIn
                ? 'The stored report could not be fetched. Pull to refresh the '
                    'queue and try again.'
                : 'Sign in to open an inspection filed on the server.'),
      );
    }
    return _ReportBody(row: row, report: report, thumbnails: thumbnails);
  }
}

/// The copied-link sheet. Deliberately shows the whole URL: the officer is about
/// to hand this to someone, and a link you cannot read before sending is a link
/// you cannot be responsible for.
class _ShareSheet extends StatelessWidget {
  const _ShareSheet({required this.url, required this.validFor});
  final String url;
  final String validFor;

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(20, 12, 20, 20),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Center(
              child: Container(
                width: 40,
                height: 4,
                decoration: BoxDecoration(
                    color: Palette.hairline, borderRadius: BorderRadius.circular(2)),
              ),
            ),
            const SizedBox(height: 16),
            Text('Printable report link',
                style: LabelJaanoTheme.display(size: 18, weight: FontWeight.w700)),
            const SizedBox(height: 6),
            Text(
              'Opens this one inspection in any browser — no sign-in needed, and '
              'use the browser\'s own Print for a PDF. Valid for $validFor, then '
              'it stops working.',
              style: Theme.of(context).textTheme.bodyMedium?.copyWith(fontSize: 13),
            ),
            const SizedBox(height: 16),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: Palette.paper,
                borderRadius: BorderRadius.circular(12),
                border: Border.all(color: Palette.hairline),
              ),
              child: SelectableText(url,
                  style: LabelJaanoTheme.readout(size: 11.5, color: Palette.navy)),
            ),
            const SizedBox(height: 14),
            SizedBox(
              width: double.infinity,
              child: FilledButton.icon(
                icon: const Icon(Icons.copy_rounded, size: 18),
                label: const Text('Copy link'),
                onPressed: () async {
                  await Clipboard.setData(ClipboardData(text: url));
                  if (!context.mounted) return;
                  Navigator.pop(context);
                  ScaffoldMessenger.of(context).showSnackBar(
                    SnackBar(content: Text('Link copied · valid for $validFor')),
                  );
                },
              ),
            ),
            const SizedBox(height: 8),
            Text(
              'The link carries a ticket scoped to this report alone. It cannot be '
              'used to sign in, and it dies with your account.',
              style: Theme.of(context)
                  .textTheme
                  .bodySmall
                  ?.copyWith(fontSize: 11.5, color: Palette.faint),
            ),
          ],
        ),
      ),
    );
  }
}

class _ReportBody extends StatelessWidget {
  const _ReportBody({
    required this.row,
    required this.report,
    required this.thumbnails,
  });

  final SavedScan row;
  final ComplianceReport report;

  /// Empty for an inspection filed elsewhere — the server keeps the findings,
  /// never the photograph.
  final List<Uint8List> thumbnails;

  @override
  Widget build(BuildContext context) {
    final r = report;

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
          const SizedBox(height: 20),
          _MetaCard(row: row),
          if (thumbnails.isNotEmpty) ...[
            const SizedBox(height: 24),
            const SectionHeader('Scanned image'),
            _Panels(thumbnails: thumbnails),
          ],
          if (row.mock) ...[
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
        const SizedBox(height: 16),
        _MetaCard(row: row),
        if (thumbnails.isNotEmpty) ...[
          const SizedBox(height: 24),
          const SectionHeader('Scanned panels'),
          _Panels(thumbnails: thumbnails),
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
        if (row.mock) ...[
          const SizedBox(height: 20),
          _MockNote(),
        ],
      ],
    );
  }
}

/// Product, place, time, note, and whether the server holds this record.
///
/// The last of those is the point: an unfiled scan cannot be shared or produced
/// later, and saying so here is kinder than a share button that 404s.
class _MetaCard extends StatelessWidget {
  const _MetaCard({required this.row});
  final SavedScan row;

  @override
  Widget build(BuildContext context) {
    final at = row.capturedAt;
    final lines = <List<String>>[
      ['Product', row.title],
      if ((row.location ?? '').trim().isNotEmpty) ['Place', row.location!.trim()],
      ['Inspected', at == null ? '—' : DateFormat('d MMM y, h:mm a').format(at)],
      ['Category', prettyCategory(row.category)],
      if ((row.note ?? '').trim().isNotEmpty) ['Note', row.note!.trim()],
    ];

    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Palette.card,
        borderRadius: BorderRadius.circular(16),
        border: const Border.fromBorderSide(BorderSide(color: Palette.hairline)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          for (final line in lines) ...[
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SizedBox(
                  width: 84,
                  child: Text(line[0].toUpperCase(),
                      style: LabelJaanoTheme.eyebrow()),
                ),
                Expanded(
                  child: Text(line[1],
                      style: Theme.of(context)
                          .textTheme
                          .bodyLarge
                          ?.copyWith(fontSize: 13.5)),
                ),
              ],
            ),
            const SizedBox(height: 10),
          ],
          Row(
            children: [
              Icon(row.filed ? Icons.lock_outline_rounded : Icons.smartphone_rounded,
                  size: 15, color: row.filed ? Palette.green : Palette.amber),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  row.filed
                      ? 'Filed on the server — retrievable and shareable.'
                      : 'Not filed — this scan lives on this device only, and is '
                          'gone when the app closes.',
                  style: Theme.of(context)
                      .textTheme
                      .bodySmall
                      ?.copyWith(fontSize: 11.5, color: Palette.muted),
                ),
              ),
            ],
          ),
        ],
      ),
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
