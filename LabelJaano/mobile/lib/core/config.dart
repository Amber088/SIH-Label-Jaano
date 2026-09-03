import 'package:flutter/foundation.dart'
    show defaultTargetPlatform, kIsWeb, TargetPlatform;

/// The backend address baked in at build time, via
/// `flutter build apk --release --dart-define=LJ_BASE_URL=https://example.com`.
///
/// This exists because [Settings] holds the base URL **in memory only** — it resets
/// to [defaultBaseUrl] on every cold start. That is harmless on the dev machine,
/// where the fallback below is already right, but it makes a hand-shared APK
/// unusable: the recipient would have to retype an address every single launch. So
/// a build destined for someone else's phone carries its server with it.
///
/// Empty (the default) means "not configured", and the per-platform fallbacks apply.
const String _configuredBaseUrl =
    String.fromEnvironment('LJ_BASE_URL', defaultValue: '');

/// The dev Mac's address on the Wi-Fi. Only used when no `LJ_BASE_URL` was given.
/// It changes whenever the network does, which is why `run.sh` prints today's value
/// and Settings can override it at runtime.
const String _lanFallbackUrl = 'http://192.168.1.104:8000';

/// Whether the app should ask the backend for its *offline mock* pipeline.
///
/// Defaults to true, unchanged, so nothing about the local workflow moves. Pass
/// `--dart-define=LJ_SERVER_MOCK=false` when building a share-ready APK pointed at a
/// backend that has a Gemini key: mock mode returns the same canned compliant label
/// for any photo, and since this setting is not persisted either, a recipient would
/// silently get canned verdicts on every launch unless they knew to flip the toggle.
const bool defaultServerMock =
    bool.fromEnvironment('LJ_SERVER_MOCK', defaultValue: true);

/// Where the FastAPI backend lives.
///
///   * A build-time `LJ_BASE_URL` wins outright — that is the shareable-APK case.
///   * Android  -> [_lanFallbackUrl], the Mac's LAN IP (this build is demoed on a
///                 physical phone over Wi-Fi; an emulator would use 10.0.2.2).
///   * iOS simulator/web -> localhost (shares the host network)
///
/// Plain `http://` to a LAN IP is fine despite Android's cleartext policy: Flutter's
/// `dart:io` sockets are not subject to `NetworkSecurityPolicy`, which only governs
/// the Java-level HTTP stack. No manifest flag is needed either way, and a hosted
/// backend will be `https://` regardless.
String defaultBaseUrl() {
  if (_configuredBaseUrl.isNotEmpty) return _configuredBaseUrl;
  if (kIsWeb) return 'http://localhost:8000';
  switch (defaultTargetPlatform) {
    case TargetPlatform.android:
      return _lanFallbackUrl;
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

  /// Fallback for the "force category" picker, used **only** until `GET /categories`
  /// answers — and if it never does, so the scan flow still works offline.
  ///
  /// Deliberately short. The server knows the real list (seventeen categories at the
  /// time of writing, and more the moment a rule pack is dropped into `rulepacks/`),
  /// so hardcoding it here guarantees drift: this list used to name four categories
  /// with hand-written hints, three of which claimed "Legal Metrology base pack" for
  /// packages that actually pull four to six packs. Anything beyond auto-detect and
  /// the two commonest cases belongs to [ApiClient.categories], not to a constant.
  static const List<CategoryOption> fallbackCategories = [
    CategoryOption.autoDetect,
    CategoryOption(id: 'packaged_food', label: 'Packaged food'),
    CategoryOption(id: 'other', label: 'Other / general'),
  ];
}

/// One entry in the category picker: either [autoDetect], or a category the server
/// reported from the packs it has actually loaded.
class CategoryOption {
  const CategoryOption({
    required this.id,
    required this.label,
    this.hint,
    this.packs = const [],
    this.declarations = 0,
    this.authorities = const [],
  });

  /// A `CategoryOut` from `GET /categories`.
  factory CategoryOption.fromJson(Map<String, dynamic> json) {
    final packs = (json['packs'] as List?)?.cast<String>() ?? const <String>[];
    final authorities =
        (json['authorities'] as List?)?.cast<String>() ?? const <String>[];
    final declarations = (json['declarations'] as num?)?.toInt() ?? 0;
    return CategoryOption(
      id: (json['id'] ?? '').toString(),
      label: (json['label'] ?? json['id'] ?? '').toString(),
      // Derived rather than sent, so the hint cannot contradict the packs the way
      // the old hardcoded strings did.
      hint: _describe(packs.length, declarations, authorities),
      packs: packs,
      declarations: declarations,
      authorities: authorities,
    );
  }

  /// The picker's first entry — the absence of a category, not a category.
  static const CategoryOption autoDetect = CategoryOption(
      id: '', label: 'Auto-detect',
      hint: 'Let the extractor classify the package');

  final String id;
  final String label;

  /// One line under the label. Null when there is nothing true to say.
  final String? hint;

  /// Rule-pack ids this category pulls in, as the server merged them.
  final List<String> packs;

  /// Mandatory declarations after merging and id-override.
  final int declarations;

  /// Regulators behind those packs, base authority first.
  final List<String> authorities;

  static String? _describe(int packs, int declarations, List<String> authorities) {
    if (packs == 0 && declarations == 0) return null;
    final who = authorities.isEmpty
        ? ''
        : ' · ${authorities.length == 1 ? _shortAuthority(authorities.first) : '${_shortAuthority(authorities.first)} +${authorities.length - 1}'}';
    return '$declarations declaration${declarations == 1 ? '' : 's'} · '
        '$packs pack${packs == 1 ? '' : 's'}$who';
  }

  /// "Food Safety and Standards Authority of India (FSSAI)" -> "FSSAI".
  static String _shortAuthority(String name) {
    final match = RegExp(r'\(([^)]{2,12})\)').firstMatch(name);
    if (match != null) return match.group(1)!;
    final dash = name.split(RegExp(r'\s+[—-]\s+')).first.trim();
    return dash.isEmpty ? name : dash;
  }
}
