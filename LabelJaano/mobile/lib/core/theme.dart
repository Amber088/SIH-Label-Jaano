import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';

/// Label Jaano's visual identity — a *measurement instrument*.
///
/// This is a Legal **Metrology** tool: its whole job is precise measurement
/// (millimetre glyph heights, panel areas, a 0–100 score). So the design leans
/// into an instrument-panel language rather than a generic app look:
///
///   * Deep authoritative **ink navy** — the colour of legal/government trust.
///   * A **brass** signature accent — the brass weights and sealed balances of
///     a weights-and-measures office. It is the one bold, on-theme risk.
///   * Every *measured value* (2.5 mm, 96.6, Rule 6(1)(c), a 14-digit licence)
///     is set in a **monospace** face so it reads like an instrument display.
///   * Verdicts use considered, slightly desaturated signal colours — never
///     neon — because an enforcement verdict should feel weighed, not alarmed.
class Palette {
  // Core neutrals ----------------------------------------------------------
  static const navy = Color(0xFF12233A); // primary / authority
  static const navySoft = Color(0xFF1D3352); // elevated navy surface
  static const navyDeep = Color(0xFF0C1727); // hero gradient floor
  static const paper = Color(0xFFF3F4F1); // app background (cool off-white)
  static const card = Color(0xFFFFFFFF); // raised surface
  static const hairline = Color(0xFFDCDFD8); // 1px dividers / borders
  static const muted = Color(0xFF5C6B7A); // secondary text
  static const faint = Color(0xFF8B98A5); // tertiary text / captions

  // Signature accent -------------------------------------------------------
  static const brass = Color(0xFFB5883B); // the weights-and-measures brass
  static const brassDeep = Color(0xFF8C6A2A);
  static const brassTint = Color(0xFFF3EAD6); // brass wash for chips

  // Verdict signals (weighed, not neon) ------------------------------------
  static const green = Color(0xFF2E7D5B); // compliant
  static const greenTint = Color(0xFFE2F0E9);
  static const amber = Color(0xFFC4862A); // needs review
  static const amberTint = Color(0xFFF6ECD9);
  static const red = Color(0xFFB23A3A); // non-compliant
  static const redTint = Color(0xFFF3E1E1);
}

class LabelJaanoTheme {
  /// Monospace "instrument readout" style — use for every measured value so
  /// numbers, rule references and codes align and read like a gauge.
  static TextStyle readout({
    double size = 14,
    FontWeight weight = FontWeight.w500,
    Color color = Palette.navy,
    double? letterSpacing,
  }) =>
      GoogleFonts.jetBrainsMono(
        fontSize: size,
        fontWeight: weight,
        color: color,
        letterSpacing: letterSpacing,
      );

  /// Technical display face — headings, the wordmark, section labels.
  static TextStyle display({
    double size = 22,
    FontWeight weight = FontWeight.w600,
    Color color = Palette.navy,
    double? letterSpacing,
    double? height,
  }) =>
      GoogleFonts.spaceGrotesk(
        fontSize: size,
        fontWeight: weight,
        color: color,
        letterSpacing: letterSpacing,
        height: height,
      );

  /// Small ALL-CAPS eyebrow label used above sections and on chips.
  static TextStyle eyebrow({Color color = Palette.faint}) => GoogleFonts.spaceGrotesk(
        fontSize: 11,
        fontWeight: FontWeight.w700,
        color: color,
        letterSpacing: 1.4,
      );

  static ThemeData build() {
    final base = ThemeData(
      useMaterial3: true,
      brightness: Brightness.light,
      scaffoldBackgroundColor: Palette.paper,
      colorScheme: const ColorScheme.light(
        primary: Palette.navy,
        onPrimary: Colors.white,
        secondary: Palette.brass,
        onSecondary: Colors.white,
        surface: Palette.card,
        onSurface: Palette.navy,
        error: Palette.red,
        onError: Colors.white,
      ),
    );

    final text = GoogleFonts.interTextTheme(base.textTheme).copyWith(
      titleLarge: display(size: 20, weight: FontWeight.w600),
      titleMedium: display(size: 16, weight: FontWeight.w600),
      bodyLarge: GoogleFonts.inter(fontSize: 15, color: Palette.navy, height: 1.45),
      bodyMedium: GoogleFonts.inter(fontSize: 14, color: Palette.muted, height: 1.45),
      labelLarge: GoogleFonts.inter(fontSize: 14, fontWeight: FontWeight.w600),
    );

    return base.copyWith(
      textTheme: text,
      appBarTheme: AppBarTheme(
        backgroundColor: Palette.paper,
        foregroundColor: Palette.navy,
        elevation: 0,
        scrolledUnderElevation: 0,
        centerTitle: false,
        titleTextStyle: display(size: 19, weight: FontWeight.w600),
      ),
      cardTheme: CardThemeData(
        color: Palette.card,
        elevation: 0,
        margin: EdgeInsets.zero,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(16),
          side: const BorderSide(color: Palette.hairline),
        ),
      ),
      dividerTheme: const DividerThemeData(
        color: Palette.hairline,
        thickness: 1,
        space: 1,
      ),
      chipTheme: ChipThemeData(
        backgroundColor: Palette.paper,
        side: const BorderSide(color: Palette.hairline),
        labelStyle: GoogleFonts.inter(fontSize: 12, color: Palette.navy),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          backgroundColor: Palette.navy,
          foregroundColor: Colors.white,
          minimumSize: const Size(0, 52),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
          textStyle: display(size: 15, weight: FontWeight.w600, color: Colors.white),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          foregroundColor: Palette.navy,
          minimumSize: const Size(0, 52),
          side: const BorderSide(color: Palette.navy, width: 1.4),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
          textStyle: display(size: 15, weight: FontWeight.w600),
        ),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: Palette.card,
        contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 14),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: Palette.hairline),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: Palette.hairline),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: Palette.brass, width: 1.6),
        ),
        labelStyle: GoogleFonts.inter(color: Palette.muted),
      ),
      snackBarTheme: SnackBarThemeData(
        backgroundColor: Palette.navy,
        contentTextStyle: GoogleFonts.inter(color: Colors.white),
        behavior: SnackBarBehavior.floating,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      ),
    );
  }
}
