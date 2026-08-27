import 'package:flutter/foundation.dart';

import '../core/config.dart';

/// Runtime, user-editable settings. Kept in memory for this session (a demo
/// build); persisting them is a one-line change once a storage lib is added.
class Settings extends ChangeNotifier {
  Settings() : _baseUrl = defaultBaseUrl();

  String _baseUrl;
  String get baseUrl => _baseUrl;
  set baseUrl(String v) {
    final next = v.trim();
    if (next == _baseUrl) return;
    _baseUrl = next;
    notifyListeners();
  }

  /// Ask the backend to use its offline mock OCR/Gemini pipeline. Default ON so
  /// the app produces real verdicts against a live server even before the heavy
  /// extraction deps (paddleocr, google-generativeai) or an API key are set up.
  /// Turn OFF for a genuine on-device-photo -> live-model scan.
  bool _serverMock = true;
  bool get serverMock => _serverMock;
  set serverMock(bool v) {
    if (v == _serverMock) return;
    _serverMock = v;
    notifyListeners();
  }

  void resetBaseUrl() {
    baseUrl = defaultBaseUrl();
  }
}
