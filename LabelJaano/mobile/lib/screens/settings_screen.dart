import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/config.dart';
import '../core/theme.dart';
import '../services/api_client.dart';
import '../services/scan_store.dart';
import '../services/settings.dart';
import '../widgets/brand.dart';
import '../widgets/common.dart';

/// Connection + pipeline settings, a live health check against the backend, a
/// rule-pack browser (audit exactly what's enforced), and session controls.
class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key});

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  final ApiClient _api = ApiClient();
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
    _api.dispose();
    _url.dispose();
    super.dispose();
  }

  Future<void> _checkHealth() async {
    final settings = context.read<Settings>();
    settings.baseUrl = _url.text;
    setState(() {
      _checking = true;
      _healthMsg = null;
    });
    try {
      final h = await _api.health(settings.baseUrl);
      final packs = h['rulepacks_loaded'] ?? h['packs_loaded'] ?? h['packs'] ?? '?';
      setState(() {
        _healthOk = true;
        _healthMsg = 'Connected · $packs rule pack(s) loaded';
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
    final settings = context.read<Settings>();
    settings.baseUrl = _url.text;
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Palette.card,
      shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
      builder: (_) => _RulePackSheet(api: _api, baseUrl: settings.baseUrl),
    );
  }

  @override
  Widget build(BuildContext context) {
    final settings = context.watch<Settings>();

    return ListView(
      padding: kPagePadding,
      children: [
        const SizedBox(height: 8),
        const Wordmark(size: 22),
        const SizedBox(height: 18),

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
            onSubmitted: (v) => context.read<Settings>().baseUrl = v,
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
                onPressed: () {
                  final def = defaultBaseUrl();
                  _url.text = def;
                  context.read<Settings>().baseUrl = def;
                },
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

        // --- Session -------------------------------------------------------
        const SectionHeader('Session'),
        _card(children: [
          ListTile(
            contentPadding: EdgeInsets.zero,
            leading: const Icon(Icons.delete_sweep_outlined, color: Palette.red),
            title: Text('Clear all inspections',
                style: LabelJaanoTheme.display(size: 15, weight: FontWeight.w600)),
            subtitle: Text('Empties the dashboard and queue for this session',
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
              'A supervisor console + field scanner. History is per-session; '
              'persistence and officer sign-in are the next milestones.'),
        ]),
      ],
    );
  }

  void _confirmClear(BuildContext context) {
    showDialog<void>(
      context: context,
      builder: (_) => AlertDialog(
        backgroundColor: Palette.card,
        title: Text('Clear all inspections?', style: LabelJaanoTheme.display(size: 18)),
        content: const Text('This removes every scan from this session. It cannot be undone.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: Palette.red),
            onPressed: () {
              context.read<ScanStore>().clear();
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
