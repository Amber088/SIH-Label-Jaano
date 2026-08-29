import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/config.dart';
import '../core/theme.dart';
import '../models/account.dart';
import '../services/api_client.dart';
import '../services/scan_store.dart';
import '../services/session.dart';
import '../services/settings.dart';
import '../widgets/brand.dart';
import '../widgets/common.dart';
import 'sign_in_screen.dart';

/// Account controls, connection + pipeline settings, a live health check against
/// the backend, and a rule-pack browser (audit exactly what's enforced).
class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key});

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  late final TextEditingController _url;

  String? _healthMsg;
  bool _healthOk = false;
  bool _checking = false;

  @override
  void initState() {
    super.initState();
    _url = TextEditingController(text: context.read<Settings>().baseUrl);
  }

  @override
  void dispose() {
    _url.dispose();
    super.dispose();
  }

  /// Point the app at a different server.
  ///
  /// Changing the address invalidates more than the address. A token minted by
  /// the old box is not just useless against the new one, it is actively
  /// misleading — the app would claim to be signed in while every request 401s —
  /// and the rows on screen describe a corpus this server has never heard of. So
  /// the session, the cached auth config and the loaded history all go with it.
  void _applyBaseUrl(String value) {
    final settings = context.read<Settings>();
    final before = settings.baseUrl;
    settings.baseUrl = value;
    if (settings.baseUrl == before) return;

    _url.text = settings.baseUrl;
    context.read<Session>().forgetServer();
    context.read<ScanStore>().reset();
    // A different server may offer entirely different sign-up options.
    context.read<Session>().loadConfig(force: true);
    setState(() {
      _healthMsg = null;
      _healthOk = false;
    });
  }

  Future<void> _checkHealth() async {
    _applyBaseUrl(_url.text);
    final settings = context.read<Settings>();
    final api = context.read<ApiClient>();
    setState(() {
      _checking = true;
      _healthMsg = null;
    });
    try {
      final h = await api.health(settings.baseUrl);
      final packs = h['rulepacks_loaded'] ?? h['packs_loaded'] ?? h['packs'] ?? '?';
      // The server says up front whether it can store anything, so the app can
      // stop offering history instead of discovering it from a 503 mid-flow.
      final history = h['history_available'];
      setState(() {
        _healthOk = true;
        _healthMsg = 'Connected · $packs rule pack(s) loaded'
            '${history == null ? '' : (history == true ? ' · records on' : ' · records off')}';
      });
    } on ApiException catch (e) {
      setState(() {
        _healthOk = false;
        _healthMsg = e.message;
      });
    } finally {
      if (mounted) setState(() => _checking = false);
    }
  }

  Future<void> _showRulePacks() async {
    _applyBaseUrl(_url.text);
    final settings = context.read<Settings>();
    // Read the shared client here rather than inside the sheet's builder: the
    // sheet outlives this frame, and the client belongs to the provider graph.
    final api = context.read<ApiClient>();
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Palette.card,
      shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
      builder: (_) => _RulePackSheet(api: api, baseUrl: settings.baseUrl),
    );
  }

  void _openSignIn() {
    Navigator.of(context).push(
      MaterialPageRoute(builder: (_) => const SignInScreen()),
    );
  }

  /// Sign out on purpose, as opposed to a session the server ended.
  ///
  /// The confirmation exists because signing out also clears the queue on screen,
  /// and an officer who has just filed something without network would lose the
  /// only copy of it. The wording says so when that is actually the case.
  Future<void> _confirmSignOut() async {
    final unfiled = context.read<ScanStore>().unfiled.length;
    final ok = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        backgroundColor: Palette.card,
        title: Text('Sign out?', style: LabelJaanoTheme.display(size: 18)),
        content: Text(unfiled == 0
            ? 'Your filed inspections stay on the server. You can sign in again '
                'at any time to bring them back.'
            : 'Your filed inspections stay on the server, but $unfiled scan'
                '${unfiled == 1 ? '' : 's'} on this device '
                '${unfiled == 1 ? 'was' : 'were'} never filed and will be lost.'),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('Cancel')),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Sign out'),
          ),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    context.read<Session>().signOut();
    // The rows belonged to that account — an officer's queue especially.
    context.read<ScanStore>().reset();
  }

  @override
  Widget build(BuildContext context) {
    final settings = context.watch<Settings>();
    final session = context.watch<Session>();

    return ListView(
      padding: kPagePadding,
      children: [
        const SizedBox(height: 8),
        const Wordmark(size: 22),
        const SizedBox(height: 18),

        // --- Account -------------------------------------------------------
        const SectionHeader('Account'),
        _card(children: [
          if (session.account != null)
            _AccountCard(
              session: session,
              onSignOut: _confirmSignOut,
            )
          else
            _SignedOutCard(
              accountsAvailable: session.accountsAvailable,
              configLoaded: session.configLoaded,
              onSignIn: _openSignIn,
            ),
        ]),
        const SizedBox(height: 22),

        // --- Connection ----------------------------------------------------
        const SectionHeader('Backend connection'),
        _card(children: [
          TextField(
            controller: _url,
            keyboardType: TextInputType.url,
            autocorrect: false,
            decoration: const InputDecoration(
              labelText: 'API base URL',
              hintText: 'http://10.0.2.2:8000',
              prefixIcon: Icon(Icons.dns_outlined),
            ),
            onSubmitted: _applyBaseUrl,
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              Expanded(
                child: FilledButton.icon(
                  onPressed: _checking ? null : _checkHealth,
                  icon: _checking
                      ? const SizedBox(
                          width: 18,
                          height: 18,
                          child: CircularProgressIndicator(
                              strokeWidth: 2, color: Colors.white))
                      : const Icon(Icons.wifi_tethering_rounded, size: 20),
                  label: Text(_checking ? 'Checking…' : 'Test connection'),
                ),
              ),
              const SizedBox(width: 10),
              OutlinedButton(
                onPressed: () => _applyBaseUrl(defaultBaseUrl()),
                child: const Text('Reset'),
              ),
            ],
          ),
          if (_healthMsg != null) ...[
            const SizedBox(height: 12),
            _StatusLine(ok: _healthOk, message: _healthMsg!),
          ],
          const SizedBox(height: 8),
          const _AddressHint(),
        ]),
        const SizedBox(height: 22),

        // --- Pipeline ------------------------------------------------------
        const SectionHeader('Extraction pipeline'),
        _card(children: [
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            activeColor: Palette.brass,
            value: settings.serverMock,
            onChanged: (v) => context.read<Settings>().serverMock = v,
            title: Text('Use server mock pipeline',
                style: LabelJaanoTheme.display(size: 15, weight: FontWeight.w600)),
            subtitle: Text(
              'When on, the backend returns verdicts from its offline mock OCR + '
              'Gemini path — so the app works with no API key or heavy deps. Turn '
              'off once paddleocr + google-generativeai and GEMINI_API_KEY are set '
              'up on the server for genuine on-photo extraction.',
              style: Theme.of(context).textTheme.bodyMedium?.copyWith(fontSize: 12.5),
            ),
          ),
          const Divider(height: 20),
          ListTile(
            contentPadding: EdgeInsets.zero,
            leading: const Icon(Icons.rule_folder_outlined, color: Palette.brassDeep),
            title: Text('Loaded rule packs',
                style: LabelJaanoTheme.display(size: 15, weight: FontWeight.w600)),
            subtitle: Text('Audit exactly which regulations are enforced',
                style: Theme.of(context).textTheme.bodyMedium?.copyWith(fontSize: 12.5)),
            trailing: const Icon(Icons.chevron_right_rounded, color: Palette.faint),
            onTap: _showRulePacks,
          ),
        ]),
        const SizedBox(height: 22),

        // --- Local records -------------------------------------------------
        const SectionHeader('Records on this device'),
        _card(children: [
          ListTile(
            contentPadding: EdgeInsets.zero,
            leading: const Icon(Icons.delete_sweep_outlined, color: Palette.red),
            title: Text('Clear device records',
                style: LabelJaanoTheme.display(size: 15, weight: FontWeight.w600)),
            subtitle: Text(
                session.isSignedIn
                    ? 'Forgets the scans held on this phone, including their photos. '
                        'Inspections filed on the server are untouched — open one and '
                        'use Delete to remove it for good.'
                    : 'Forgets every scan taken on this phone. Nothing was filed, so '
                        'this cannot be undone.',
                style: Theme.of(context).textTheme.bodyMedium?.copyWith(fontSize: 12.5)),
            onTap: () => _confirmClear(context),
          ),
        ]),
        const SizedBox(height: 22),

        // --- About ---------------------------------------------------------
        const SectionHeader('About'),
        _card(children: [
          _aboutRow('Enforces', AppInfo.basis),
          const Divider(height: 20),
          _aboutRow('Build', 'Label Jaano mobile · v0.1.0 (SIH prototype)'),
          const Divider(height: 20),
          _aboutRow('Note',
              'A supervisor console + field scanner. Sign in to file inspections '
              'to the server, search them, and share a report by link; scan '
              'anonymously and everything stays on this phone.'),
        ]),
      ],
    );
  }

  void _confirmClear(BuildContext context) {
    showDialog<void>(
      context: context,
      builder: (_) => AlertDialog(
        backgroundColor: Palette.card,
        title:
            Text('Clear device records?', style: LabelJaanoTheme.display(size: 18)),
        content: Text(context.read<Session>().isSignedIn
            ? 'This removes the scans held on this phone and their photos. Filed '
                'inspections stay on the server and will come back on the next '
                'refresh.'
            : 'This removes every scan from this session. It cannot be undone.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: Palette.red),
            onPressed: () {
              context.read<ScanStore>().clearLocal();
              Navigator.pop(context);
            },
            child: const Text('Clear'),
          ),
        ],
      ),
    );
  }

  Widget _aboutRow(String label, String value) => Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 76,
            child: Text(label.toUpperCase(), style: LabelJaanoTheme.eyebrow()),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Text(value, style: Theme.of(context).textTheme.bodyLarge?.copyWith(fontSize: 13.5)),
          ),
        ],
      );

  Widget _card({required List<Widget> children}) => Container(
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
          color: Palette.card,
          borderRadius: BorderRadius.circular(16),
          border: const Border.fromBorderSide(BorderSide(color: Palette.hairline)),
        ),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: children),
      );
}

/// The signed-in account: who the server thinks you are, what that lets you see,
/// and how much longer the token lasts.
///
/// The role shown is [Account.roleTitle] — the server's own wording — rather than
/// the app's enum label, so a role added server-side after this build still reads
/// correctly instead of falling back to "Account".
class _AccountCard extends StatelessWidget {
  const _AccountCard({required this.session, required this.onSignOut});

  final Session session;
  final Future<void> Function() onSignOut;

  @override
  Widget build(BuildContext context) {
    final Account account = session.account!;
    final auth = session.session!;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Container(
              width: 46,
              height: 46,
              alignment: Alignment.center,
              decoration: BoxDecoration(
                color: account.role.tint,
                shape: BoxShape.circle,
                border: Border.all(color: account.role.color, width: 1.5),
              ),
              child: Text(account.initials,
                  style: LabelJaanoTheme.display(
                      size: 15, weight: FontWeight.w700, color: account.role.color)),
            ),
            const SizedBox(width: 14),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(account.displayName,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style:
                          LabelJaanoTheme.display(size: 16, weight: FontWeight.w700)),
                  const SizedBox(height: 3),
                  Text(account.email,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: LabelJaanoTheme.readout(size: 11.5, color: Palette.muted)),
                ],
              ),
            ),
          ],
        ),
        const SizedBox(height: 12),
        Row(
          children: [
            ReadoutChip(account.roleTitle.toUpperCase(),
                color: account.role.color, bg: account.role.tint),
            const SizedBox(width: 6),
            ReadoutChip(
                session.seesEveryScan ? 'ALL INSPECTIONS' : 'OWN INSPECTIONS',
                color: Palette.muted),
          ],
        ),
        const SizedBox(height: 10),
        Text(account.role.gloss,
            style: Theme.of(context).textTheme.bodyMedium?.copyWith(fontSize: 12.5)),
        const Divider(height: 22),
        _row(context, Icons.timer_outlined, 'Signed in for another '
            '${_format(auth.remaining)}'),
        if (session.config.ephemeralSecret)
          _row(
            context,
            Icons.info_outline_rounded,
            'This server signs tokens with a per-process key, so restarting it '
            'signs everyone out. Expected on a demo box.',
          ),
        // Only reachable if an admin disabled the account between refreshes; the
        // next request ends the session, but say so rather than looking healthy.
        if (account.disabled)
          _row(context, Icons.block_rounded,
              'This account has been disabled on the server.'),
        const SizedBox(height: 6),
        Align(
          alignment: Alignment.centerLeft,
          child: OutlinedButton.icon(
            onPressed: onSignOut,
            icon: const Icon(Icons.logout_rounded, size: 18),
            label: const Text('Sign out'),
          ),
        ),
      ],
    );
  }

  Widget _row(BuildContext context, IconData icon, String text) => Padding(
        padding: const EdgeInsets.only(bottom: 8),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Icon(icon, size: 15, color: Palette.faint),
            const SizedBox(width: 8),
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

  static String _format(Duration d) {
    if (d.inMinutes < 1) return 'under a minute';
    if (d.inHours < 1) return '${d.inMinutes} min';
    final minutes = d.inMinutes.remainder(60);
    return minutes == 0 ? '${d.inHours}h' : '${d.inHours}h ${minutes}m';
  }
}

/// Anonymous mode, stated as a choice rather than a locked door — scanning works
/// either way, and the card says what signing in would add.
class _SignedOutCard extends StatelessWidget {
  const _SignedOutCard({
    required this.accountsAvailable,
    required this.configLoaded,
    required this.onSignIn,
  });

  final bool accountsAvailable;
  final bool configLoaded;
  final VoidCallback onSignIn;

  @override
  Widget build(BuildContext context) {
    final body = Theme.of(context).textTheme.bodyMedium?.copyWith(fontSize: 12.5);

    if (configLoaded && !accountsAvailable) {
      return Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Icon(Icons.person_off_outlined, color: Palette.muted, size: 20),
              const SizedBox(width: 10),
              Text('Accounts unavailable',
                  style: LabelJaanoTheme.display(size: 15, weight: FontWeight.w600)),
            ],
          ),
          const SizedBox(height: 8),
          Text(
              'This server is running without a records database, so there is '
              'nothing to sign in to. Scanning and verdicts work exactly the same; '
              'results stay on this phone.',
              style: body),
        ],
      );
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            const Icon(Icons.person_outline_rounded, color: Palette.brassDeep, size: 20),
            const SizedBox(width: 10),
            Text('Scanning as a guest',
                style: LabelJaanoTheme.display(size: 15, weight: FontWeight.w600)),
          ],
        ),
        const SizedBox(height: 8),
        Text(
            'Verdicts work without an account. Signing in files each inspection to '
            'the server so it survives closing the app, makes it searchable, and '
            'lets you share a report by link. Officers additionally see every '
            'inspection filed on this server.',
            style: body),
        const SizedBox(height: 12),
        FilledButton.icon(
          onPressed: configLoaded ? onSignIn : null,
          icon: const Icon(Icons.login_rounded, size: 18),
          label: Text(configLoaded ? 'Sign in or create account' : 'Checking server…'),
        ),
      ],
    );
  }
}

class _StatusLine extends StatelessWidget {
  const _StatusLine({required this.ok, required this.message});
  final bool ok;
  final String message;

  @override
  Widget build(BuildContext context) {
    final color = ok ? Palette.green : Palette.red;
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: ok ? Palette.greenTint : Palette.redTint,
        borderRadius: BorderRadius.circular(10),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(ok ? Icons.check_circle_rounded : Icons.error_rounded, color: color, size: 18),
          const SizedBox(width: 10),
          Expanded(
            child: Text(message,
                style: Theme.of(context)
                    .textTheme
                    .bodyMedium
                    ?.copyWith(color: color, fontSize: 12.5)),
          ),
        ],
      ),
    );
  }
}

class _AddressHint extends StatelessWidget {
  const _AddressHint();
  @override
  Widget build(BuildContext context) {
    Widget row(String head, String body) => Padding(
          padding: const EdgeInsets.only(top: 6),
          child: RichText(
            text: TextSpan(
              style: Theme.of(context).textTheme.bodyMedium?.copyWith(fontSize: 12),
              children: [
                TextSpan(text: '$head  ', style: LabelJaanoTheme.readout(size: 11.5, color: Palette.navy)),
                TextSpan(text: body),
              ],
            ),
          ),
        );
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: Palette.paper,
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: Palette.hairline),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('WHICH ADDRESS?', style: LabelJaanoTheme.eyebrow()),
          row('10.0.2.2:8000', 'Android emulator → your Mac'),
          row('localhost:8000', 'iOS simulator'),
          row('192.168.x.x:8000', "Physical phone → your Mac's Wi-Fi/LAN IP"),
        ],
      ),
    );
  }
}

class _RulePackSheet extends StatelessWidget {
  const _RulePackSheet({required this.api, required this.baseUrl});
  final ApiClient api;
  final String baseUrl;

  @override
  Widget build(BuildContext context) {
    return DraggableScrollableSheet(
      expand: false,
      initialChildSize: 0.6,
      maxChildSize: 0.9,
      builder: (context, controller) => FutureBuilder<List<Map<String, dynamic>>>(
        future: api.rulePacks(baseUrl),
        builder: (context, snap) {
          return Column(
            children: [
              const SizedBox(height: 10),
              Container(
                  width: 40,
                  height: 4,
                  decoration: BoxDecoration(
                      color: Palette.hairline, borderRadius: BorderRadius.circular(2))),
              Padding(
                padding: const EdgeInsets.all(16),
                child: Row(
                  children: [
                    const Icon(Icons.rule_folder_rounded, color: Palette.brassDeep),
                    const SizedBox(width: 10),
                    Text('Loaded rule packs',
                        style: LabelJaanoTheme.display(size: 18, weight: FontWeight.w700)),
                  ],
                ),
              ),
              Expanded(child: _body(context, controller, snap)),
            ],
          );
        },
      ),
    );
  }

  Widget _body(
    BuildContext context,
    ScrollController controller,
    AsyncSnapshot<List<Map<String, dynamic>>> snap,
  ) {
    if (snap.connectionState == ConnectionState.waiting) {
      return const Center(child: CircularProgressIndicator(color: Palette.brass));
    }
    if (snap.hasError) {
      return EmptyState(
        icon: Icons.cloud_off_rounded,
        title: 'Could not load rule packs',
        message: '${snap.error}',
      );
    }
    final packs = snap.data ?? const [];
    if (packs.isEmpty) {
      return const EmptyState(
        icon: Icons.inbox_rounded,
        title: 'No rule packs reported',
        message: 'The server returned an empty list.',
      );
    }
    return ListView.separated(
      controller: controller,
      padding: const EdgeInsets.fromLTRB(16, 0, 16, 24),
      itemCount: packs.length,
      separatorBuilder: (_, __) => const SizedBox(height: 10),
      itemBuilder: (context, i) {
        final p = packs[i];
        final scope = (p['scope'] ?? 'category').toString();
        return Container(
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(
            color: Palette.paper,
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: Palette.hairline),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Text((p['label'] ?? p['pack_id'] ?? 'pack').toString(),
                        style: LabelJaanoTheme.display(size: 15, weight: FontWeight.w600)),
                  ),
                  ReadoutChip(scope == 'base' ? 'BASE' : 'CATEGORY',
                      color: scope == 'base' ? Palette.navy : Palette.brassDeep,
                      bg: scope == 'base' ? Palette.paper : Palette.brassTint),
                ],
              ),
              const SizedBox(height: 6),
              if (p['authority'] != null)
                Text(p['authority'].toString(),
                    style: Theme.of(context).textTheme.bodyMedium?.copyWith(fontSize: 12.5)),
              const SizedBox(height: 8),
              Row(
                children: [
                  ReadoutChip((p['pack_id'] ?? '').toString(), color: Palette.muted),
                  const SizedBox(width: 6),
                  if (p['version'] != null)
                    ReadoutChip('v${p['version']}', color: Palette.muted),
                  if (p['declarations'] != null) ...[
                    const SizedBox(width: 6),
                    ReadoutChip('${p['declarations']} declarations', color: Palette.muted),
                  ],
                ],
              ),
            ],
          ),
        );
      },
    );
  }
}
