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
      return 'http://192.168.1.104:8000';
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
