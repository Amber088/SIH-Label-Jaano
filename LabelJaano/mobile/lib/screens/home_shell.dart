import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/theme.dart';
import '../services/scan_store.dart';
import '../services/session.dart';
import 'dashboard_screen.dart';
import 'queue_screen.dart';
import 'scan_screen.dart';
import 'settings_screen.dart';
import 'sign_in_screen.dart';

/// The app's root: a four-tab shell (Dashboard · Scan · Queue · Settings) with
/// the capture flow given pride of place in the centre. Screens are kept alive
/// via IndexedStack so a half-filled scan form survives a tab switch.
///
/// The shell also owns the two pieces of session housekeeping that belong to no
/// single tab: asking the server what sign-in options it offers before any screen
/// has to decide whether to show a sign-in button, and telling the user when a
/// session ended by itself.
class HomeShell extends StatefulWidget {
  const HomeShell({super.key});

  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> with WidgetsBindingObserver {
  int _index = 0;

  void _goToScan() => setState(() => _index = 1);
  void _goToQueue() => setState(() => _index = 2);

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      // Cheap, never throws, and every screen's sign-in affordance depends on the
      // answer. A server running with LABEL_JAANO_NO_DB=1 has no accounts at all,
      // and offering a form that cannot work is worse than offering nothing.
      context.read<Session>().loadConfig();
    });
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) _onResume();
  }

  /// Coming back to the foreground is the moment to reconcile with the server: a
  /// phone in a pocket between two shops may have been away long enough for the
  /// token to age, or for an admin to have changed the account.
  ///
  /// Sequenced, not parallel. [Session.refreshIfNeeded] replaces the token;
  /// [Session.revalidate] captures the session it starts with and writes it back
  /// with a fresh account attached. Run together, the slower one would restore the
  /// token the other had just replaced.
  Future<void> _onResume() async {
    final session = context.read<Session>();
    if (!session.isSignedIn) return;
    await session.refreshIfNeeded();
    if (!mounted || !session.isSignedIn) return;
    await session.revalidate();
  }

  void _openSignIn() {
    Navigator.of(context).push(
      MaterialPageRoute(builder: (_) => const SignInScreen()),
    );
  }

  /// Say why a session ended, once, wherever the user happens to be standing.
  ///
  /// Sessions can end without anyone asking: the token expires, an admin disables
  /// the account, or a demo server restarts with a fresh signing key. Left
  /// unexplained, the app simply appears to have forgotten the officer mid-shift.
  void _announce(Session session) {
    final notice = session.endedNotice;
    if (notice == null) return;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      // The rows on screen belonged to the session that just ended, and an
      // officer's queue spans other people's inspections — leaving it up would be
      // both confusing and a small disclosure.
      context.read<ScanStore>().reset();
      session.clearEndedNotice();
      ScaffoldMessenger.of(context)
        ..clearSnackBars()
        ..showSnackBar(
          SnackBar(
            content: Text(notice),
            backgroundColor: Palette.navyDeep,
            duration: const Duration(seconds: 6),
            action: SnackBarAction(
              label: 'Sign in',
              textColor: Palette.brassTint,
              onPressed: _openSignIn,
            ),
          ),
        );
    });
  }

  @override
  Widget build(BuildContext context) {
    _announce(context.watch<Session>());

    final pages = [
      DashboardScreen(onScan: _goToScan, onSeeQueue: _goToQueue),
      ScanScreen(onDone: _goToQueue),
      const QueueScreen(),
      const SettingsScreen(),
    ];

    return Scaffold(
      body: SafeArea(bottom: false, child: IndexedStack(index: _index, children: pages)),
      bottomNavigationBar: NavigationBarTheme(
        data: NavigationBarThemeData(
          backgroundColor: Palette.card,
          indicatorColor: Palette.brassTint,
          labelTextStyle: WidgetStateProperty.resolveWith(
            (states) => LabelJaanoTheme.display(
              size: 11.5,
              weight: FontWeight.w600,
              color: states.contains(WidgetState.selected) ? Palette.navy : Palette.muted,
            ),
          ),
          iconTheme: WidgetStateProperty.resolveWith(
            (states) => IconThemeData(
              color: states.contains(WidgetState.selected) ? Palette.brassDeep : Palette.muted,
            ),
          ),
        ),
        child: NavigationBar(
          height: 66,
          selectedIndex: _index,
          onDestinationSelected: (i) => setState(() => _index = i),
          destinations: const [
            NavigationDestination(
                icon: Icon(Icons.speed_outlined),
                selectedIcon: Icon(Icons.speed_rounded),
                label: 'Dashboard'),
            NavigationDestination(
                icon: Icon(Icons.center_focus_weak_outlined),
                selectedIcon: Icon(Icons.center_focus_strong_rounded),
                label: 'Scan'),
            NavigationDestination(
                icon: Icon(Icons.inbox_outlined),
                selectedIcon: Icon(Icons.inbox_rounded),
                label: 'Queue'),
            NavigationDestination(
                icon: Icon(Icons.tune_outlined),
                selectedIcon: Icon(Icons.tune_rounded),
                label: 'Settings'),
          ],
        ),
      ),
    );
  }
}
