import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import 'core/theme.dart';
import 'services/scan_store.dart';
import 'services/settings.dart';
import 'screens/home_shell.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();

  // Make any widget-build error visible ON THE DEVICE (a readable, scrollable
  // card) instead of a blank body or the terse red overlay — and echo the full
  // details to the run log so it can be diagnosed from the terminal too.
  ErrorWidget.builder = (FlutterErrorDetails details) {
    return Material(
      color: const Color(0xFFFFF4F4),
      child: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(20),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text(
                'Render error',
                style: TextStyle(
                    fontSize: 18,
                    fontWeight: FontWeight.w700,
                    color: Color(0xFFB23A3A)),
              ),
              const SizedBox(height: 12),
              Text(
                '${details.exception}',
                style: const TextStyle(fontSize: 13, color: Color(0xFF12233A)),
              ),
              const SizedBox(height: 12),
              Text(
                details.stack?.toString() ?? '',
                style: const TextStyle(
                    fontSize: 10,
                    fontFamily: 'monospace',
                    color: Color(0xFF5C6B7A)),
              ),
            ],
          ),
        ),
      ),
    );
  };
  FlutterError.onError = (FlutterErrorDetails details) {
    FlutterError.presentError(details); // keep the default console/log dump
  };

  SystemChrome.setSystemUIOverlayStyle(const SystemUiOverlayStyle(
    statusBarColor: Colors.transparent,
    statusBarIconBrightness: Brightness.light, // over the navy hero
  ));
  runApp(const LabelJaanoApp());
}

class LabelJaanoApp extends StatelessWidget {
  const LabelJaanoApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MultiProvider(
      providers: [
        ChangeNotifierProvider(create: (_) => Settings()),
        ChangeNotifierProvider(create: (_) => ScanStore()),
      ],
      child: MaterialApp(
        title: 'Label Jaano',
        debugShowCheckedModeBanner: false,
        theme: LabelJaanoTheme.build(),
        home: const HomeShell(),
      ),
    );
  }
}
