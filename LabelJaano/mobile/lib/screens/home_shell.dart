import 'package:flutter/material.dart';

import '../core/theme.dart';
import 'dashboard_screen.dart';
import 'queue_screen.dart';
import 'scan_screen.dart';
import 'settings_screen.dart';

/// The app's root: a four-tab shell (Dashboard · Scan · Queue · Settings) with
/// the capture flow given pride of place in the centre. Screens are kept alive
/// via IndexedStack so a half-filled scan form survives a tab switch.
class HomeShell extends StatefulWidget {
  const HomeShell({super.key});

  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  int _index = 0;

  void _goToScan() => setState(() => _index = 1);
  void _goToQueue() => setState(() => _index = 2);

  @override
  Widget build(BuildContext context) {
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
