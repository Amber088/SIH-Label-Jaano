import 'package:flutter/material.dart';

import '../core/theme.dart';

/// A strip of ruler graduations — the app's recurring "measurement" motif.
/// Used as a decorative rule under headers and across the hero, tying the
/// whole UI back to *metrology* (measurement) without a literal ruler icon.
class RulerTicks extends StatelessWidget {
  const RulerTicks({
    super.key,
    this.height = 14,
    this.color = Palette.hairline,
    this.majorEvery = 5,
    this.spacing = 8,
  });

  final double height;
  final Color color;
  final int majorEvery;
  final double spacing;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: height,
      width: double.infinity,
      child: CustomPaint(
        painter: _TickPainter(
          color: color,
          majorEvery: majorEvery,
          spacing: spacing,
        ),
      ),
    );
  }
}

class _TickPainter extends CustomPainter {
  _TickPainter({
    required this.color,
    required this.majorEvery,
    required this.spacing,
  });

  final Color color;
  final int majorEvery;
  final double spacing;

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = color
      ..strokeWidth = 1.2
      ..strokeCap = StrokeCap.round;
    var i = 0;
    for (double x = 0; x <= size.width; x += spacing) {
      final isMajor = i % majorEvery == 0;
      final h = isMajor ? size.height : size.height * 0.5;
      canvas.drawLine(Offset(x, size.height - h), Offset(x, size.height), paint);
      i++;
    }
  }

  @override
  bool shouldRepaint(covariant _TickPainter old) =>
      old.color != color || old.spacing != spacing || old.majorEvery != majorEvery;
}

/// The Label Jaano wordmark: a brass measurement bracket + the name set in the
/// technical display face. Compact enough for an app bar, legible on navy.
class Wordmark extends StatelessWidget {
  const Wordmark({super.key, this.onDark = false, this.size = 20});

  final bool onDark;
  final double size;

  @override
  Widget build(BuildContext context) {
    final fg = onDark ? Colors.white : Palette.navy;
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        _Caliper(color: Palette.brass, size: size + 6),
        const SizedBox(width: 9),
        RichText(
          text: TextSpan(
            children: [
              TextSpan(
                text: 'Label ',
                style: LabelJaanoTheme.display(
                    size: size, weight: FontWeight.w700, color: fg),
              ),
              TextSpan(
                text: 'Jaano',
                style: LabelJaanoTheme.display(
                    size: size, weight: FontWeight.w700, color: Palette.brass),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

/// A tiny caliper/measurement glyph used as the app's mark.
class _Caliper extends StatelessWidget {
  const _Caliper({required this.color, required this.size});
  final Color color;
  final double size;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: size,
      height: size,
      child: CustomPaint(painter: _CaliperPainter(color)),
    );
  }
}

class _CaliperPainter extends CustomPainter {
  _CaliperPainter(this.color);
  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    final p = Paint()
      ..color = color
      ..strokeWidth = 2.2
      ..strokeCap = StrokeCap.round
      ..style = PaintingStyle.stroke;
    final w = size.width, h = size.height;
    // Top beam
    canvas.drawLine(Offset(w * 0.12, h * 0.24), Offset(w * 0.88, h * 0.24), p);
    // Two jaws dropping from the beam
    canvas.drawLine(Offset(w * 0.24, h * 0.24), Offset(w * 0.24, h * 0.78), p);
    canvas.drawLine(Offset(w * 0.62, h * 0.24), Offset(w * 0.62, h * 0.60), p);
    // Measured object tick
    final dot = Paint()..color = color;
    canvas.drawCircle(Offset(w * 0.62, h * 0.74), 2.4, dot);
  }

  @override
  bool shouldRepaint(covariant _CaliperPainter old) => old.color != color;
}
