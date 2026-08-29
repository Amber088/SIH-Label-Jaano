import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/theme.dart';
import '../models/account.dart';
import '../services/scan_store.dart';
import '../services/session.dart';
import '../services/settings.dart';
import '../widgets/common.dart';

/// Sign in, or enrol.
///
/// Reached from Settings and from the prompt on the Queue tab — never forced.
/// Nothing in this app requires an account: scanning, verdicts and rule packs all
/// work anonymously, and an account adds history, export and (for an officer) the
/// corpus-wide view on top. So this screen is always dismissible, and says what
/// signing in is *for* rather than demanding it.
///
/// The form is drawn from `/auth/config`, fetched on entry. A server running
/// without a database has no accounts at all, and one with no officer enrolment
/// code configured cannot create officers — in both cases the screen says so
/// instead of offering a button that is going to fail.
class SignInScreen extends StatefulWidget {
  const SignInScreen({super.key, this.startOnSignUp = false});

  final bool startOnSignUp;

  @override
  State<SignInScreen> createState() => _SignInScreenState();
}

class _SignInScreenState extends State<SignInScreen> {
  final _form = GlobalKey<FormState>();
  final _email = TextEditingController();
  final _password = TextEditingController();
  final _name = TextEditingController();
  final _officerCode = TextEditingController();

  late bool _signUp = widget.startOnSignUp;
  Role _role = Role.consumer;
  bool _obscure = true;

  @override
  void initState() {
    super.initState();
    // force: the address may have changed since the last time this was asked, and
    // a different server can have entirely different sign-up rules.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) context.read<Session>().loadConfig(force: true);
    });
  }

  @override
  void dispose() {
    _email.dispose();
    _password.dispose();
    _name.dispose();
    _officerCode.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final session = context.read<Session>();
    if (!(_form.currentState?.validate() ?? false)) return;
    FocusScope.of(context).unfocus();

    final ok = _signUp
        ? await session.signUp(
            email: _email.text,
            password: _password.text,
            name: _name.text,
            role: _role,
            officerCode: _officerCode.text,
          )
        : await session.signIn(email: _email.text, password: _password.text);

    if (!mounted || !ok) return;
    // Pull the account's history straight away: signing in should land on a
    // populated queue, not an empty one that fills in when you next tap a tab.
    context.read<ScanStore>().sync();
    Navigator.of(context).pop(true);
  }

  @override
  Widget build(BuildContext context) {
    final session = context.watch<Session>();
    final settings = context.watch<Settings>();
    final config = session.config;
    final officerAvailable = config.officerSignupEnabled;

    return Scaffold(
      appBar: AppBar(
        title: Text(_signUp ? 'Create an account' : 'Sign in'),
      ),
      body: ListView(
        padding: kPagePadding,
        children: [
          const _Intro(),
          const SizedBox(height: 20),

          if (!session.configLoaded)
            const _Notice(
              icon: Icons.sync_rounded,
              text: 'Checking what this server offers…',
            )
          else if (!config.accountsAvailable)
            _Notice(
              icon: Icons.cloud_off_rounded,
              tone: Palette.amber,
              text: 'This server is running without a database, so it has no '
                  'accounts. Scanning still works — history, export and the '
                  'officer view need a server with persistence enabled.\n\n'
                  'Address: ${settings.baseUrl}',
            )
          else
            _buildForm(session, config, officerAvailable),

          if (config.accountsAvailable && config.ephemeralSecret) ...[
            const SizedBox(height: 14),
            const _Notice(
              icon: Icons.info_outline_rounded,
              tone: Palette.muted,
              text: 'This server signs tokens with a temporary key, so every '
                  'restart signs everyone out. Set LABEL_JAANO_SECRET to keep '
                  'sessions across restarts.',
            ),
          ],
        ],
      ),
    );
  }

  Widget _buildForm(Session session, AuthConfig config, bool officerAvailable) {
    return Form(
      key: _form,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (_signUp) ...[
            const SectionHeader('Who is signing up'),
            _RoleChoice(
              value: _role,
              officerAvailable: officerAvailable,
              onChanged: (r) => setState(() => _role = r),
            ),
            if (!officerAvailable) ...[
              const SizedBox(height: 10),
              const _Notice(
                icon: Icons.lock_outline_rounded,
                tone: Palette.muted,
                text: 'Officer enrolment is switched off on this server. Set '
                    'LABEL_JAANO_OFFICER_CODE to enable it, or promote an '
                    'account with manage.py.',
              ),
            ],
            const SizedBox(height: 22),
          ],

          const SectionHeader('Credentials'),
          if (_signUp) ...[
            TextFormField(
              controller: _name,
              textCapitalization: TextCapitalization.words,
              decoration: const InputDecoration(
                labelText: 'Name',
                hintText: 'Printed on inspection reports you file',
                prefixIcon: Icon(Icons.badge_outlined),
              ),
            ),
            const SizedBox(height: 14),
          ],
          TextFormField(
            controller: _email,
            keyboardType: TextInputType.emailAddress,
            autocorrect: false,
            decoration: const InputDecoration(
              labelText: 'Email',
              prefixIcon: Icon(Icons.alternate_email_rounded),
            ),
            validator: (v) {
              final value = (v ?? '').trim();
              if (value.isEmpty) return 'Enter your email address';
              // Deliberately loose. The server is the authority on what it will
              // accept, and a clever regex here would only reject addresses that
              // are perfectly valid.
              if (!value.contains('@') || value.endsWith('@')) {
                return "That doesn't look like an email address";
              }
              return null;
            },
          ),
          const SizedBox(height: 14),
          TextFormField(
            controller: _password,
            obscureText: _obscure,
            decoration: InputDecoration(
              labelText: 'Password',
              prefixIcon: const Icon(Icons.key_outlined),
              suffixIcon: IconButton(
                icon: Icon(_obscure
                    ? Icons.visibility_outlined
                    : Icons.visibility_off_outlined),
                onPressed: () => setState(() => _obscure = !_obscure),
                tooltip: _obscure ? 'Show password' : 'Hide password',
              ),
              helperText: _signUp
                  ? 'At least ${config.minPasswordLength} characters'
                  : null,
            ),
            onFieldSubmitted: (_) => _submit(),
            validator: (v) {
              final value = v ?? '';
              if (value.isEmpty) return 'Enter your password';
              // Only enforced on sign-up: an existing account may predate the
              // current rule, and refusing to *log in* would lock them out.
              if (_signUp && value.length < config.minPasswordLength) {
                return 'Use at least ${config.minPasswordLength} characters';
              }
              return null;
            },
          ),

          if (_signUp && _role == Role.officer && officerAvailable) ...[
            const SizedBox(height: 14),
            TextFormField(
              controller: _officerCode,
              autocorrect: false,
              decoration: const InputDecoration(
                labelText: 'Officer enrolment code',
                hintText: 'Issued by your department',
                prefixIcon: Icon(Icons.verified_user_outlined),
              ),
              validator: (v) => (v ?? '').trim().isEmpty
                  ? 'The enrolment code is required for an officer account'
                  : null,
            ),
          ],

          if (session.error != null) ...[
            const SizedBox(height: 16),
            _Notice(
              icon: Icons.error_outline_rounded,
              tone: Palette.red,
              text: session.error!,
            ),
          ],

          const SizedBox(height: 24),
          SizedBox(
            height: 52,
            child: ElevatedButton(
              onPressed: session.busy ? null : _submit,
              child: session.busy
                  ? const SizedBox(
                      width: 20,
                      height: 20,
                      child: CircularProgressIndicator(
                          strokeWidth: 2, color: Colors.white),
                    )
                  : Text(_signUp ? 'Create account' : 'Sign in'),
            ),
          ),
          const SizedBox(height: 12),
          Center(
            child: TextButton(
              onPressed: session.busy
                  ? null
                  : () {
                      context.read<Session>().clearError();
                      setState(() {
                        _signUp = !_signUp;
                        if (!_signUp) _role = Role.consumer;
                      });
                    },
              child: Text(_signUp
                  ? 'I already have an account'
                  : 'Create an account instead'),
            ),
          ),
        ],
      ),
    );
  }
}

class _Intro extends StatelessWidget {
  const _Intro();

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Palette.brassTint,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: Palette.hairline),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('Scanning works without an account',
              style: LabelJaanoTheme.display(size: 15, weight: FontWeight.w600)),
          const SizedBox(height: 8),
          Text(
            'Sign in to keep a history of your inspections, export a print-ready '
            'report, and — as a Legal Metrology officer — review every inspection '
            'filed on this server.',
            style: Theme.of(context).textTheme.bodyMedium,
          ),
        ],
      ),
    );
  }
}

class _RoleChoice extends StatelessWidget {
  const _RoleChoice({
    required this.value,
    required this.officerAvailable,
    required this.onChanged,
  });

  final Role value;
  final bool officerAvailable;
  final ValueChanged<Role> onChanged;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        _RoleCard(
          role: Role.consumer,
          selected: value == Role.consumer,
          enabled: true,
          onTap: () => onChanged(Role.consumer),
        ),
        const SizedBox(height: 10),
        _RoleCard(
          role: Role.officer,
          selected: value == Role.officer,
          enabled: officerAvailable,
          onTap: officerAvailable ? () => onChanged(Role.officer) : null,
        ),
      ],
    );
  }
}

class _RoleCard extends StatelessWidget {
  const _RoleCard({
    required this.role,
    required this.selected,
    required this.enabled,
    this.onTap,
  });

  final Role role;
  final bool selected;
  final bool enabled;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final accent = enabled ? role.color : Palette.faint;
    return Opacity(
      opacity: enabled ? 1 : 0.55,
      child: Material(
        color: selected ? role.tint : Palette.card,
        borderRadius: BorderRadius.circular(14),
        child: InkWell(
          borderRadius: BorderRadius.circular(14),
          onTap: onTap,
          child: Container(
            padding: const EdgeInsets.all(14),
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(14),
              border: Border.all(
                color: selected ? accent : Palette.hairline,
                width: selected ? 1.6 : 1,
              ),
            ),
            child: Row(
              children: [
                Icon(
                  role == Role.officer
                      ? Icons.account_balance_outlined
                      : Icons.shopping_basket_outlined,
                  color: accent,
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(role.label,
                          style: LabelJaanoTheme.display(
                              size: 14.5, weight: FontWeight.w600)),
                      const SizedBox(height: 3),
                      Text(role.gloss,
                          style: Theme.of(context)
                              .textTheme
                              .bodyMedium
                              ?.copyWith(fontSize: 12.5)),
                    ],
                  ),
                ),
                Icon(
                  selected
                      ? Icons.radio_button_checked_rounded
                      : Icons.radio_button_off_rounded,
                  color: selected ? accent : Palette.faint,
                  size: 20,
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

/// A bordered advisory line. Used for every non-fatal thing this screen has to
/// say, so they all look like the same kind of message.
class _Notice extends StatelessWidget {
  const _Notice({required this.icon, required this.text, this.tone = Palette.navy});
  final IconData icon;
  final String text;
  final Color tone;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(13),
      decoration: BoxDecoration(
        color: Palette.card,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: Palette.hairline),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, size: 18, color: tone),
          const SizedBox(width: 10),
          Expanded(
            child: Text(text,
                style: Theme.of(context)
                    .textTheme
                    .bodyMedium
                    ?.copyWith(fontSize: 12.5, height: 1.45)),
          ),
        ],
      ),
    );
  }
}
