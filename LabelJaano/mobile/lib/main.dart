import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import 'core/theme.dart';
import 'services/api_client.dart';
import 'services/scan_store.dart';
import 'services/session.dart';
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
    // Order is load-bearing, not stylistic. Settings owns the server address;
    // Session needs to read it at call time; ScanStore needs both. Each provider
    // below therefore only reaches backwards in this list, never forwards.
    //
    // The address is passed as a closure (`() => ...baseUrl`) rather than a value
    // so that changing the server in Settings takes effect on the very next
    // request. Handing over a string here would freeze whatever it happened to be
    // at startup, and the symptom — Settings saying one thing while the app dials
    // another — is a miserable thing to debug on a demo stage.
    return MultiProvider(
      providers: [
        ChangeNotifierProvider(create: (_) => Settings()),
        Provider<ApiClient>(
          create: (_) => ApiClient(),
          dispose: (_, client) => client.dispose(),
        ),
        ChangeNotifierProvider<Session>(
          create: (ctx) => Session(
            api: ctx.read<ApiClient>(),
            baseUrl: () => ctx.read<Settings>().baseUrl,
          ),
        ),
        ChangeNotifierProvider<ScanStore>(
          create: (ctx) => ScanStore(
            api: ctx.read<ApiClient>(),
            baseUrl: () => ctx.read<Settings>().baseUrl,
            session: ctx.read<Session>(),
          ),
        ),
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
