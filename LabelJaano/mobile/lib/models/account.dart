import 'dart:ui' show Color;

import '../core/theme.dart';

/// Accounts, as the API describes them.
///
/// Dart mirror of `UserOut`, `TokenOut` and `AuthConfigOut` in
/// backend/app/schemas.py. Keys match 1:1 so decoding is a straight read.
///
/// A note on where authority lives: nothing in this file grants anything. The
/// server resolves the caller's role from its own database on every request, so
/// [Role] here is only good for deciding what to *draw* — which tabs to show, how
/// to word the queue header. If the app and the server ever disagree, the server
/// wins and the app gets a 403. Never gate a security decision on this enum.

enum Role {
  consumer,
  officer,
  admin,
  unknown;

  static Role fromJson(String? v) => switch (v) {
        'consumer' => Role.consumer,
        'officer' => Role.officer,
        'admin' => Role.admin,
        _ => Role.unknown,
      };

  String get wire => switch (this) {
        Role.consumer => 'consumer',
        Role.officer => 'officer',
        Role.admin => 'admin',
        Role.unknown => 'consumer',
      };

  String get label => switch (this) {
        Role.consumer => 'Consumer',
        Role.officer => 'Legal Metrology Officer',
        Role.admin => 'Administrator',
        Role.unknown => 'Account',
      };

  /// Short form for a chip, where the full title does not fit.
  String get shortLabel => switch (this) {
        Role.consumer => 'Consumer',
        Role.officer => 'Officer',
        Role.admin => 'Admin',
        Role.unknown => 'Account',
      };

  String get gloss => switch (this) {
        Role.consumer => 'Scan labels and keep a private history of your checks',
        Role.officer =>
          'Review every inspection filed on this server and export reports',
        Role.admin => 'Full access, including rule-pack reloads',
        Role.unknown => '',
      };

  Color get color => switch (this) {
        Role.consumer => Palette.brass,
        Role.officer => Palette.navy,
        Role.admin => Palette.navyDeep,
        Role.unknown => Palette.muted,
      };

  Color get tint => switch (this) {
        Role.consumer => Palette.brassTint,
        Role.officer => Palette.hairline,
        Role.admin => Palette.hairline,
        Role.unknown => Palette.hairline,
      };

  /// Whether this role sees the whole corpus rather than just its own scans.
  /// Presentation only — the server scopes the query regardless.
  bool get seesEveryScan => this == Role.officer || this == Role.admin;
}

class Account {
  const Account({
    required this.id,
    required this.email,
    required this.name,
    required this.role,
    this.roleLabel = '',
    this.createdAt = '',
    this.disabled = false,
  });

  final String id;
  final String email;
  final String name;
  final Role role;

  /// The server's own wording for the role. Preferred over [Role.label] when
  /// showing it back to the user, so a future role added server-side still reads
  /// correctly in an app build that predates it.
  final String roleLabel;
  final String createdAt;
  final bool disabled;

  factory Account.fromJson(Map<String, dynamic> json) => Account(
        id: (json['id'] ?? '').toString(),
        email: (json['email'] ?? '').toString(),
        name: (json['name'] ?? '').toString(),
        role: Role.fromJson(json['role'] as String?),
        roleLabel: (json['role_label'] ?? '').toString(),
        createdAt: (json['created_at'] ?? '').toString(),
        disabled: json['disabled'] == true,
      );

  /// What to call this person on screen. Falls back through name, then the local
  /// part of the address, so a card is never headed by a blank line.
  String get displayName {
    if (name.trim().isNotEmpty) return name.trim();
    final at = email.indexOf('@');
    return at > 0 ? email.substring(0, at) : email;
  }

  String get roleTitle => roleLabel.isNotEmpty ? roleLabel : role.label;

  /// Two letters for an avatar.
  String get initials {
    final parts = displayName
        .split(RegExp(r'[\s._-]+'))
        .where((p) => p.isNotEmpty)
        .toList();
    if (parts.isEmpty) return '?';
    if (parts.length == 1) {
      final one = parts.first;
      return (one.length > 1 ? one.substring(0, 2) : one).toUpperCase();
    }
    return (parts[0][0] + parts[1][0]).toUpperCase();
  }
}

/// A signed-in session: the bearer token plus who it belongs to.
class AuthSession {
  const AuthSession({
    required this.token,
    required this.account,
    required this.expiresAt,
  });

  final String token;
  final Account account;

  /// Absolute local time this token stops working, derived from the server's
  /// `expires_in` at the moment of decode. Stored absolute rather than as a
  /// duration so it stays meaningful while the app sits in the background.
  final DateTime expiresAt;

  factory AuthSession.fromJson(Map<String, dynamic> json) {
    final seconds = (json['expires_in'] as num?)?.toInt() ?? 0;
    return AuthSession(
      token: (json['access_token'] ?? '').toString(),
      account: Account.fromJson(
          (json['user'] as Map?)?.cast<String, dynamic>() ?? const {}),
      expiresAt: DateTime.now().add(Duration(seconds: seconds)),
    );
  }

  AuthSession copyWith({String? token, Account? account, DateTime? expiresAt}) =>
      AuthSession(
        token: token ?? this.token,
        account: account ?? this.account,
        expiresAt: expiresAt ?? this.expiresAt,
      );

  bool get isExpired => DateTime.now().isAfter(expiresAt);

  /// True while there is still time to slide the session forward. Refreshing
  /// early is the point — waiting for expiry means the officer discovers it
  /// mid-inspection.
  bool get isNearingExpiry =>
      expiresAt.difference(DateTime.now()) < const Duration(hours: 2);

  Duration get remaining {
    final left = expiresAt.difference(DateTime.now());
    return left.isNegative ? Duration.zero : left;
  }
}

/// What the server will accept at sign-up, fetched before the form is drawn so
/// the app never offers an option that is going to 403.
class AuthConfig {
  const AuthConfig({
    required this.accountsAvailable,
    required this.officerSignupEnabled,
    required this.minPasswordLength,
    required this.ephemeralSecret,
  });

  final bool accountsAvailable;
  final bool officerSignupEnabled;
  final int minPasswordLength;

  /// True when the server signs tokens with a per-process key, so every restart
  /// logs everyone out. Worth saying out loud on a demo box, otherwise the app
  /// looks broken when uvicorn reloads.
  final bool ephemeralSecret;

  /// Assumed when `/auth/config` cannot be reached: accounts off, so the app
  /// stays in anonymous mode rather than showing a sign-in form that cannot work.
  static const AuthConfig unavailable = AuthConfig(
    accountsAvailable: false,
    officerSignupEnabled: false,
    minPasswordLength: 8,
    ephemeralSecret: false,
  );

  factory AuthConfig.fromJson(Map<String, dynamic> json) => AuthConfig(
        accountsAvailable: json['accounts_available'] == true,
        officerSignupEnabled: json['officer_signup_enabled'] == true,
        minPasswordLength:
            (json['min_password_length'] as num?)?.toInt() ?? 8,
        ephemeralSecret: json['ephemeral_secret'] == true,
      );
}
