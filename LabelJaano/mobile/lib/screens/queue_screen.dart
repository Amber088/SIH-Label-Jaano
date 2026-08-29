import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import '../core/theme.dart';
import '../models/compliance_report.dart';
import '../models/saved_scan.dart';
import '../services/scan_store.dart';
import '../services/session.dart';
import '../widgets/common.dart';
import '../widgets/scan_tile.dart';
import 'report_screen.dart';
import 'sign_in_screen.dart';

/// The inspection queue: newest first, filterable by verdict, searchable.
///
/// What it contains depends on who is looking, and the server decides that — an
/// officer's queue spans every inspection filed on the server, a consumer's holds
/// their own, and with no account it holds whatever was scanned on this device
/// this session. The screen reports which of those it is showing rather than
/// inferring it from the role, because the server is the authority and the UI
/// lagging behind it is how someone ends up mistrusting the numbers.
class QueueScreen extends StatefulWidget {
  const QueueScreen({super.key});

  @override
  State<QueueScreen> createState() => _QueueScreenState();
}

class _QueueScreenState extends State<QueueScreen> {
  Verdict? _filter; // null = all
  final TextEditingController _search = TextEditingController();

  /// The search actually applied to the loaded page, as opposed to whatever is
  /// currently half-typed in the box.
  String _applied = '';

  @override
  void initState() {
    super.initState();
    // First look at the tab should already have the history in it. Sign-in also
    // kicks off a sync, so this covers the other order: signed in, then arrives.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      final store = context.read<ScanStore>();
      if (store.syncedAt == null) store.sync();
    });
  }

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  Future<void> _refresh() async {
    _applied = _search.text.trim();
    await context.read<ScanStore>().sync(search: _applied.isEmpty ? null : _applied);
  }

  void _openSignIn() {
    Navigator.of(context).push(
      MaterialPageRoute(builder: (_) => const SignInScreen()),
    );
  }

  @override
  Widget build(BuildContext context) {
    final store = context.watch<ScanStore>();
    final session = context.watch<Session>();
    final all = store.rows;
    final items =
        _filter == null ? all : all.where((r) => r.verdict == _filter).toList();

    return Column(
      children: [
        _Header(store: store, session: session),
        _SearchBar(
          controller: _search,
          enabled: session.isSignedIn,
          onSubmit: _refresh,
        ),
        _Filters(
          selected: _filter,
          counts: store.verdictCounts,
          total: store.total,
          onSelect: (v) => setState(() => _filter = v),
        ),
        const SizedBox(height: 4),
        if (store.syncError != null)
          _Banner(
            icon: Icons.cloud_off_rounded,
            tone: Palette.amber,
            text: store.syncError!,
          ),
        if (store.hasUnloadedRows)
          _Banner(
            icon: Icons.layers_outlined,
            tone: Palette.muted,
            text: 'Showing the ${store.rows.length} most recent of '
                '${store.serverTotal} — search to narrow the list.',
          ),
        Expanded(
          child: RefreshIndicator(
            color: Palette.brassDeep,
            onRefresh: _refresh,
            child: _list(items, store, session),
          ),
        ),
      ],
    );
  }

  /// Always a scrollable, even when there is nothing in it — otherwise
  /// pull-to-refresh stops working in exactly the state where the user most wants
  /// to try again.
  Widget _list(List<SavedScan> items, ScanStore store, Session session) {
    if (items.isEmpty) {
      return ListView(
        padding: const EdgeInsets.only(top: 40),
        children: [_emptyState(store, session)],
      );
    }
    return ListView.separated(
      padding: kPagePadding,
      itemCount: items.length,
      separatorBuilder: (_, __) => const SizedBox(height: 10),
      itemBuilder: (context, i) {
        final row = items[i];
        return ScanTile(
          row: row,
          // Photos live only on the device that took the scan.
          thumbnail: _thumbnailFor(store, row),
          ownerLabel: _ownerLabel(store, session, row),
          onTap: () => Navigator.of(context).push(
            MaterialPageRoute(builder: (_) => ReportScreen(recordId: row.id)),
          ),
        );
      },
    );
  }

  static Uint8List? _thumbnailFor(ScanStore store, SavedScan row) {
    final local = store.localFor(row.id);
    final shots = local?.thumbnails ?? const <Uint8List>[];
    return shots.isEmpty ? null : shots.first;
  }

  /// Who filed it — shown only in a queue that spans other inspectors' work.
  ///
  /// A short reference rather than an email address: the list endpoint returns
  /// account ids, and it is enough to tell two inspectors apart while triaging,
  /// which is all this line is for.
  String? _ownerLabel(ScanStore store, Session session, SavedScan row) {
    if (!store.spansEveryone) return null;
    final owner = (row.userId ?? '').trim();
    if (owner.isEmpty) return null;
    if (owner == session.account?.id) return 'you';
    return 'inspector ${owner.length <= 6 ? owner : owner.substring(0, 6)}';
  }

  Widget _emptyState(ScanStore store, Session session) {
    if (store.syncing) {
      return const Padding(
        padding: EdgeInsets.all(40),
        child: Center(child: CircularProgressIndicator()),
      );
    }
    if (_filter != null && !store.isEmpty) {
      return EmptyState(
        icon: Icons.filter_alt_off_rounded,
        title: 'Nothing matches this filter',
        message: 'No inspections with the “${_filter!.label}” verdict here.',
        action: OutlinedButton(
          onPressed: () => setState(() => _filter = null),
          child: const Text('Show all'),
        ),
      );
    }
    if (_applied.isNotEmpty) {
      return EmptyState(
        icon: Icons.search_off_rounded,
        title: 'No match for “$_applied”',
        message: 'Search covers the product, place and note recorded with each '
            'inspection.',
        action: OutlinedButton(
          onPressed: () {
            _search.clear();
            _refresh();
          },
          child: const Text('Clear search'),
        ),
      );
    }
    if (!session.isSignedIn) {
      return EmptyState(
        icon: Icons.inbox_rounded,
        title: 'Nothing scanned yet',
        message: 'Inspections you run appear here. Sign in to keep them past this '
            'session and to open the ones filed on other devices.',
        action: FilledButton(
          onPressed: _openSignIn,
          child: const Text('Sign in'),
        ),
      );
    }
    return const EmptyState(
      icon: Icons.inbox_rounded,
      title: 'The queue is empty',
      message: 'Inspections you file appear here for triage and follow-up.',
    );
  }
}

class _Header extends StatelessWidget {
  const _Header({required this.store, required this.session});
  final ScanStore store;
  final Session session;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 12, 20, 8),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(store.spansEveryone ? 'OFFICER QUEUE' : 'INSPECTIONS',
                    style: LabelJaanoTheme.eyebrow(color: Palette.muted)),
                const SizedBox(height: 2),
                Text(store.spansEveryone ? 'Every inspection' : 'Inspections',
                    style: LabelJaanoTheme.display(size: 22, weight: FontWeight.w700)),
                const SizedBox(height: 4),
                Text(_scopeLine(),
                    style: LabelJaanoTheme.readout(size: 11, color: Palette.faint)),
              ],
            ),
          ),
          if (store.syncing)
            const Padding(
              padding: EdgeInsets.only(top: 6),
              child: SizedBox(
                  width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2)),
            )
          else if (!store.isEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 6),
              child: Text('${store.total} shown',
                  style: LabelJaanoTheme.readout(size: 13, color: Palette.muted)),
            ),
        ],
      ),
    );
  }

  /// One line saying whose records these are and how fresh they are. Both halves
  /// matter: an officer triaging needs to know the list is the whole corpus, and
  /// anyone pulling to refresh wants to see that it worked.
  String _scopeLine() {
    if (!session.isSignedIn) {
      return 'This device, this session · not filed';
    }
    final scope = store.spansEveryone
        ? 'Every account on this server'
        : 'Your own inspections';
    final at = store.syncedAt;
    if (at == null) return scope;
    return '$scope · synced ${DateFormat('h:mm a').format(at)}';
  }
}

class _SearchBar extends StatelessWidget {
  const _SearchBar({
    required this.controller,
    required this.enabled,
    required this.onSubmit,
  });

  final TextEditingController controller;

  /// Search runs on the server, so it needs an account. Disabled rather than
  /// hidden: it explains itself instead of quietly not being there.
  final bool enabled;
  final VoidCallback onSubmit;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 0, 20, 10),
      // Listening to the controller so the clear button appears the moment there
      // is something to clear, rather than on the next unrelated rebuild.
      child: ValueListenableBuilder<TextEditingValue>(
        valueListenable: controller,
        builder: (context, value, _) => TextField(
          controller: controller,
          enabled: enabled,
          textInputAction: TextInputAction.search,
          onSubmitted: (_) => onSubmit(),
          decoration: InputDecoration(
            isDense: true,
            hintText: enabled
                ? 'Search product, place or note'
                : 'Sign in to search filed inspections',
            prefixIcon: const Icon(Icons.search_rounded, size: 20),
            suffixIcon: value.text.isEmpty
                ? null
                : IconButton(
                    icon: const Icon(Icons.close_rounded, size: 18),
                    onPressed: () {
                      controller.clear();
                      onSubmit();
                    },
                  ),
          ),
        ),
      ),
    );
  }
}

/// A one-line advisory strip above the list — sync trouble, or a page that does
/// not hold everything the server does.
class _Banner extends StatelessWidget {
  const _Banner({required this.icon, required this.text, required this.tone});
  final IconData icon;
  final String text;
  final Color tone;

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.fromLTRB(20, 0, 20, 8),
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(
        color: Palette.card,
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: Palette.hairline),
      ),
      child: Row(
        children: [
          Icon(icon, size: 16, color: tone),
          const SizedBox(width: 10),
          Expanded(
            child: Text(text,
                style: Theme.of(context)
                    .textTheme
                    .bodySmall
                    ?.copyWith(fontSize: 11.5, color: Palette.muted)),
          ),
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
          chip('Compliant', Verdict.compliant, counts[Verdict.compliant] ?? 0,
              Palette.green),
          chip('Needs review', Verdict.needsReview, counts[Verdict.needsReview] ?? 0,
              Palette.amber),
          chip('Non-compliant', Verdict.nonCompliant, counts[Verdict.nonCompliant] ?? 0,
              Palette.red),
        ],
      ),
    );
  }
}
