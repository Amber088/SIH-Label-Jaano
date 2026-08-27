import 'package:flutter/foundation.dart'
    show defaultTargetPlatform, kIsWeb, TargetPlatform;

/// Where the FastAPI backend lives, resolved per-platform so the app "just works"
/// out of the box:
///
///   * Android  -> the Mac's LAN IP (this build is demoed on a physical phone
///                 over Wi-Fi; an emulator would instead use 10.0.2.2). Change
///                 this if the Mac's IP changes, or override it in Settings.
///   * iOS simulator/web -> localhost (shares the host network)
String defaultBaseUrl() {
  if (kIsWeb) return 'http://localhost:8000';
  switch (defaultTargetPlatform) {
    case TargetPlatform.android:
      return 'http://192.168.1.112:8000';
    default:
      return 'http://localhost:8000';
  }
}

/// Static, compile-time app facts. Mutable runtime settings (base URL, server
/// mock toggle) live in [Settings] so they can be changed from the UI.
class AppInfo {
  static const String appName = 'Label Jaano';
  static const String tagline = 'Legal Metrology label compliance, in seconds';

  /// The primary regulation this build enforces (shown in the About/Settings).
  static const String basis = 'Legal Metrology (Packaged Commodities) Rules, 2011';

  /// Category ids the backend understands today (base pack + food pack).
  /// Used to populate the "force category" picker in the scan flow.
  static const List<CategoryOption> categories = [
    CategoryOption('', 'Auto-detect', 'Let the extractor classify the package'),
    CategoryOption('packaged_food', 'Packaged food', 'Legal Metrology + FSSAI food pack'),
    CategoryOption('packaged_water', 'Packaged water', 'Legal Metrology base pack'),
    CategoryOption('cosmetics', 'Cosmetics', 'Legal Metrology base pack'),
    CategoryOption('other', 'Other / general', 'Legal Metrology base pack only'),
  ];
}

class CategoryOption {
  const CategoryOption(this.id, this.label, this.hint);
  final String id;
  final String label;
  final String hint;
}
