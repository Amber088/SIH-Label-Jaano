import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../core/theme.dart';
import '../models/compliance_report.dart';

/// The compliance score rendered as a precision instrument dial — a 270° gauge
/// with graduation ticks and a needle, the number set in the monospace readout
/// face. This is the app's signature "you're reading a measurement" moment.
class ScoreGauge extends StatelessWidget {
  const ScoreGauge({
    super.key,
    required this.score,
    required this.verdict,
    this.size = 190,
    this.showLabel = true,
  });

  final double score;
  final Verdict verdict;
  final double size;
  final bool showLabel;

  @override
  Widget build(BuildContext context) {
    final clamped = score.clamp(0, 100).toDouble();
    return SizedBox(
      width: size,
      height: size,
      child: CustomPaint(
        painter: _GaugePainter(value: clamped, color: verdict.color),
        child: Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text.rich(
                TextSpan(children: [
                  TextSpan(
                    text: clamped.toStringAsFixed(clamped == clamped.roundToDouble() ? 0 : 1),
                    style: LabelJaanoTheme.readout(
                      size: size * 0.24,
                      weight: FontWeight.w700,
                      color: Palette.navy,
                    ),
                  ),
                  TextSpan(
                    text: ' /100',
                    style: LabelJaanoTheme.readout(
                      size: size * 0.09,
                      weight: FontWeight.w500,
                      color: Palette.faint,
                    ),
                  ),
                ]),
              ),
              if (showLabel) ...[
                const SizedBox(height: 2),
                Text('COMPLIANCE', style: LabelJaanoTheme.eyebrow()),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

class _GaugePainter extends CustomPainter {
  _GaugePainter({required this.value, required this.color});

  final double value; // 0..100
  final Color color;

  static const double _start = 3 * math.pi / 4; // 135°
  static const double _sweep = 3 * math.pi / 2; // 270°

  @override
  void paint(Canvas canvas, Size size) {
    final center = Offset(size.width / 2, size.height / 2);
    final radius = size.width / 2 - 14;
    final rect = Rect.fromCircle(center: center, radius: radius);

    // Track
    final track = Paint()
      ..color = Palette.hairline
      ..style = PaintingStyle.stroke
      ..strokeWidth = 10
      ..strokeCap = StrokeCap.round;
    canvas.drawArc(rect, _start, _sweep, false, track);

    // Value arc
    final valuePaint = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = 10
      ..strokeCap = StrokeCap.round;
    canvas.drawArc(rect, _start, _sweep * (value / 100.0), false, valuePaint);

    // Graduation ticks (0..100 by 10)
    final tickPaint = Paint()
      ..color = Palette.faint
      ..strokeWidth = 1.4
      ..strokeCap = StrokeCap.round;
    for (int i = 0; i <= 10; i++) {
      final a = _start + _sweep * (i / 10.0);
      final outer = radius + 2;
      final inner = radius - (i % 5 == 0 ? 10 : 6);
      final p1 = center + Offset(math.cos(a) * outer, math.sin(a) * outer);
      final p2 = center + Offset(math.cos(a) * inner, math.sin(a) * inner);
      canvas.drawLine(p1, p2, tickPaint);
    }

    // Needle
    final needleAngle = _start + _sweep * (value / 100.0);
    final needlePaint = Paint()
      ..color = Palette.navy
      ..strokeWidth = 3
      ..strokeCap = StrokeCap.round;
    final tip = center + Offset(math.cos(needleAngle) * (radius - 16),
        math.sin(needleAngle) * (radius - 16));
    final tail = center - Offset(math.cos(needleAngle) * 14, math.sin(needleAngle) * 14);
    canvas.drawLine(tail, tip, needlePaint);
    canvas.drawCircle(center, 5.5, Paint()..color = Palette.navy);
    canvas.drawCircle(center, 2.5, Paint()..color = color);
  }

  @override
  bool shouldRepaint(covariant _GaugePainter old) =>
      old.value != value || old.color != color;
}
