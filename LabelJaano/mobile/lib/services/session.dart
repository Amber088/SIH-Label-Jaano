import 'package:flutter/foundation.dart';

import '../models/account.dart';
import 'api_client.dart';

/// Who is signed in, if anyone.
///
/// **The session lives in memory only, and that is a decision rather than an
/// omission.** Persisting it would mean adding a platform plugin (secure storage
/// or shared_preferences), and a bearer token in `SharedPreferences` is plain text
/// on disk. More to the point: the server already holds every inspection, so
/// signing in again re-hydrates the whole history in one request. Nothing is lost
/// by forgetting the token — only a re-login is — whereas a token cached in the
/// clear is a lasting liability. Sign-in survives navigation and backgrounding;
/// it does not survive a cold start.
///
/// Anonymity is a first-class state here, not a failure to sign in. [account] is
/// null and everything still works — scanning, verdicts, rule packs — because
/// consumer mode is half the product. Screens should ask [isSignedIn] to decide
/// what to *offer*, never to decide whether to function.
class Session extends ChangeNotifier {
  Session({required ApiClient api, required String Function() baseUrl})
      : _api = api,
        _baseUrl = baseUrl;

  final ApiClient _api;

  /// Read at call time rather than captured, so changing the server address in
  /// Settings takes effect on the next request instead of the next app launch.
  final String Function() _baseUrl;

  AuthSession? _session;
  AuthConfig _config = AuthConfig.unavailable;
  bool _configLoaded = false;
  bool _busy = false;
  String? _error;

  /// A message worth putting in front of the user *outside* a form — set when a
  /// session ends by itself (expiry, an account disabled, a server restart with
  /// an ephemeral secret) rather than because they asked to sign out.
  String? _endedNotice;

  AuthSession? get session => _session;
  Account? get account => _session?.account;
  String? get token => _session?.token;
  bool get isSignedIn => _session != null;

  /// Presentation only. The server resolves authority from its own database on
  /// every request, so this decides what to draw, never what is permitted.
  Role get role => account?.role ?? Role.unknown;
  bool get seesEveryScan => role.seesEveryScan;

  AuthConfig get config => _config;
  bool get configLoaded => _configLoaded;

  /// Whether this server offers accounts at all. False on a box running with
  /// `LABEL_JAANO_NO_DB=1`, where the sign-in screen should not be reachable.
  bool get accountsAvailable => _config.accountsAvailable;

  bool get busy => _busy;
  String? get error => _error;
  String? get endedNotice => _endedNotice;

  /// Ask the server what sign-up options it offers. Cheap, never throws, and
  /// worth re-running whenever the address changes — a different server may have
  /// entirely different answers.
  Future<AuthConfig> loadConfig({bool force = false}) async {
    if (_configLoaded && !force) return _config;
    _config = await _api.authConfig(_baseUrl());
    _configLoaded = true;
    notifyListeners();
    return _config;
  }

  /// Called when the server address changes: the old config and any token
  /// belonged to a different server, and a token minted elsewhere is not just
  /// useless but confusing — it would 401 on every request while the app claimed
  /// to be signed in.
  void forgetServer() {
    _session = null;
    _config = AuthConfig.unavailable;
    _configLoaded = false;
    _error = null;
    _endedNotice = null;
    notifyListeners();
  }

  Future<bool> signIn({required String email, required String password}) =>
      _attempt(() => _api.login(
            baseUrl: _baseUrl(),
            email: email,
            password: password,
          ));

  Future<bool> signUp({
    required String email,
    required String password,
    String name = '',
    Role role = Role.consumer,
    String? officerCode,
  }) =>
      _attempt(() => _api.register(
            baseUrl: _baseUrl(),
            email: email,
            password: password,
            name: name,
            role: role,
            officerCode: officerCode,
          ));

  /// Run a sign-in/sign-up attempt, surfacing the outcome as a bool and the
  /// reason as [error]. Returns false rather than throwing because both callers
  /// are forms, and a form wants a message next to the button, not an exception.
  Future<bool> _attempt(Future<AuthSession> Function() run) async {
    _busy = true;
    _error = null;
    _endedNotice = null;
    notifyListeners();
    try {
      _session = await run();
      return true;
    } on ApiException catch (e) {
      _error = e.message;
      return false;
    } finally {
      _busy = false;
      notifyListeners();
    }
  }

  void clearError() {
    if (_error == null) return;
    _error = null;
    notifyListeners();
  }

  void clearEndedNotice() {
    if (_endedNotice == null) return;
    _endedNotice = null;
    notifyListeners();
  }

  void signOut() {
    if (_session == null) return;
    _session = null;
    _error = null;
    _endedNotice = null;
    notifyListeners();
  }

  /// Drop the session because the *server* rejected it, and say why.
  ///
  /// Every service that makes an authenticated call routes its 401s here. That
  /// centralisation is the point: without it, one screen would clear the token
  /// while another kept using it, and the app would be half signed in — which
  /// looks to the user like features randomly not working.
  void endSession(String reason) {
    if (_session == null) return;
    _session = null;
    _endedNotice = reason;
    notifyListeners();
  }

  /// Hand any [ApiException] here; a 401 ends the session, anything else is
  /// returned unchanged for the caller to display. Returns true if it was
  /// handled as an auth failure.
  bool handleFailure(ApiException e) {
    if (!e.isAuthFailure || _session == null) return false;
    endSession(e.message);
    return true;
  }

  /// Re-check the token and pick up any server-side change to the account.
  ///
  /// Worth calling when the app comes back to the foreground. A role granted by
  /// an admin, or an account disabled, both take effect server-side immediately —
  /// this is how the UI stops lagging behind that.
  Future<void> revalidate() async {
    final current = _session;
    if (current == null) return;
    try {
      final fresh = await _api.me(baseUrl: _baseUrl(), token: current.token);
      if (fresh.disabled) {
        endSession('This account has been disabled.');
        return;
      }
      _session = current.copyWith(account: fresh);
      notifyListeners();
    } on ApiException catch (e) {
      if (!handleFailure(e)) {
        // A network blip or a 503 is not a reason to log anyone out. Leave the
        // session alone; the next real request will report the problem itself.
      }
    }
  }

  /// Slide the session forward if it is close to expiring.
  ///
  /// Deliberately silent on failure. This runs opportunistically in the
  /// background, and a failed refresh means only that the existing token has to
  /// live out its remaining time — not something to interrupt an inspection over.
  Future<void> refreshIfNeeded() async {
    final current = _session;
    if (current == null || !current.isNearingExpiry) return;
    if (current.isExpired) {
      endSession('Your session expired. Please sign in again.');
      return;
    }
    try {
      _session = await _api.refresh(baseUrl: _baseUrl(), token: current.token);
      notifyListeners();
    } on ApiException catch (e) {
      handleFailure(e);
    }
  }
}
