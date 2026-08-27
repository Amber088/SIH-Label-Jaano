import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:provider/provider.dart';

import '../core/config.dart';
import '../core/theme.dart';
import '../models/scan_record.dart';
import '../services/api_client.dart';
import '../services/scan_store.dart';
import '../services/settings.dart';
import '../widgets/brand.dart';
import '../widgets/common.dart';
import 'report_screen.dart';

/// A demo calibration for the bundled sample labels (backend/samples/*.png):
/// a manual mm-per-pixel + principal-display-panel box so Rule 8 font heights
/// are actually *measured* in the demo. For a real photo you'd instead place a
/// reference card/ArUco in frame; without any reference the engine simply skips
/// the font-height checks rather than guessing.
const Map<String, dynamic> kSampleCalibration = {
  'type': 'manual',
  'mm_per_px': 0.0531,
  'pdp_bbox': [40, 40, 820, 1220],
};

class _Picked {
  _Picked(this.bytes, this.filename);
  final Uint8List bytes;
  final String filename;
}

/// The officer's core action: capture the front (and optionally back) panel,
/// set any options, and run a compliance scan against the live backend.
class ScanScreen extends StatefulWidget {
  const ScanScreen({super.key, required this.onDone});

  /// Called after a successful scan (used to surface the queue tab).
  final VoidCallback onDone;

  @override
  State<ScanScreen> createState() => _ScanScreenState();
}

class _ScanScreenState extends State<ScanScreen> {
  final ApiClient _api = ApiClient();
  final ImagePicker _picker = ImagePicker();
  final TextEditingController _note = TextEditingController();

  _Picked? _front;
  _Picked? _back;
  String _category = '';
  bool _useSampleCalibration = false;
  bool _busy = false;

  @override
  void dispose() {
    _api.dispose();
    _note.dispose();
    super.dispose();
  }

  Future<void> _pick(bool isFront) async {
    final source = await _chooseSource();
    if (source == null) return;
    try {
      final x = await _picker.pickImage(
        source: source,
        maxWidth: 2200,
        imageQuality: 90,
      );
      if (x == null) return;
      final bytes = await x.readAsBytes();
      setState(() {
        final picked = _Picked(bytes, x.name.isEmpty ? 'label.jpg' : x.name);
        if (isFront) {
          _front = picked;
        } else {
          _back = picked;
        }
      });
    } catch (e) {
      if (mounted) _toast('Could not open the camera/gallery: $e');
    }
  }

  Future<ImageSource?> _chooseSource() {
    return showModalBottomSheet<ImageSource>(
      context: context,
      backgroundColor: Palette.card,
      shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
      builder: (_) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const SizedBox(height: 8),
            Container(
                width: 40,
                height: 4,
                decoration: BoxDecoration(
                    color: Palette.hairline, borderRadius: BorderRadius.circular(2))),
            ListTile(
              leading: const Icon(Icons.camera_alt_rounded, color: Palette.navy),
              title: const Text('Take a photo'),
              onTap: () => Navigator.pop(context, ImageSource.camera),
            ),
            ListTile(
              leading: const Icon(Icons.photo_library_rounded, color: Palette.navy),
              title: const Text('Choose from gallery'),
              onTap: () => Navigator.pop(context, ImageSource.gallery),
            ),
            const SizedBox(height: 8),
          ],
        ),
      ),
    );
  }

  Future<void> _runScan() async {
    if (_front == null) {
      _toast('Add the front panel photo first.');
      return;
    }
    final settings = context.read<Settings>();
    final store = context.read<ScanStore>();

    setState(() => _busy = true);
    try {
      final images = <LabelImage>[
        LabelImage(_front!.bytes, _front!.filename),
        if (_back != null) LabelImage(_back!.bytes, _back!.filename),
      ];
      final report = await _api.scanImage(
        baseUrl: settings.baseUrl,
        images: images,
        category: _category,
        serverMock: settings.serverMock,
        reference: _useSampleCalibration ? kSampleCalibration : null,
      );

      final record = ScanRecord(
        id: DateTime.now().microsecondsSinceEpoch.toString(),
        capturedAt: DateTime.now(),
        report: report,
        thumbnails: [_front!.bytes, if (_back != null) _back!.bytes],
        note: _note.text.trim().isEmpty ? null : _note.text.trim(),
        serverMock: settings.serverMock,
      );
      store.add(record);

      if (!mounted) return;
      // Reset the form for the next package.
      setState(() {
        _front = null;
        _back = null;
        _note.clear();
        _useSampleCalibration = false;
      });
      widget.onDone();
      Navigator.of(context).push(
        MaterialPageRoute(builder: (_) => ReportScreen(recordId: record.id)),
      );
    } on ApiException catch (e) {
      if (mounted) _errorDialog(e.message);
    } catch (e) {
      if (mounted) _errorDialog('Unexpected error: $e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _toast(String msg) =>
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));

  void _errorDialog(String message) {
    showDialog<void>(
      context: context,
      builder: (_) => AlertDialog(
        backgroundColor: Palette.card,
        title: Row(
          children: [
            const Icon(Icons.error_outline_rounded, color: Palette.red),
            const SizedBox(width: 10),
            Text('Scan failed', style: LabelJaanoTheme.display(size: 18)),
          ],
        ),
        content: Text(message, style: Theme.of(context).textTheme.bodyLarge),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('Dismiss'),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final settings = context.watch<Settings>();

    return Stack(
      children: [
        ListView(
          padding: kPagePadding,
          children: [
            const SizedBox(height: 8),
            Text('CAPTURE', style: LabelJaanoTheme.eyebrow(color: Palette.muted)),
            const SizedBox(height: 4),
            Text('Scan a package label',
                style: LabelJaanoTheme.display(size: 24, weight: FontWeight.w700)),
            const SizedBox(height: 6),
            Text(
              'Photograph the front (principal display panel). Add the back panel '
              'for the detailed declarations.',
              style: Theme.of(context).textTheme.bodyMedium,
            ),
            const SizedBox(height: 20),
            _CaptureSlot(
              label: 'Front panel (PDP)',
              hint: 'Brand, net quantity, MRP',
              picked: _front?.bytes,
              required: true,
              onTap: () => _pick(true),
              onClear: _front == null ? null : () => setState(() => _front = null),
            ),
            const SizedBox(height: 12),
            _CaptureSlot(
              label: 'Back panel',
              hint: 'Ingredients, FSSAI, dates — optional',
              picked: _back?.bytes,
              required: false,
              onTap: () => _pick(false),
              onClear: _back == null ? null : () => setState(() => _back = null),
            ),
            const SizedBox(height: 20),
            _options(settings),
            const SizedBox(height: 24),
            FilledButton.icon(
              onPressed: _busy ? null : _runScan,
              icon: const Icon(Icons.center_focus_strong_rounded, size: 20),
              label: Text(_busy ? 'Scanning…' : 'Run compliance scan'),
            ),
            const SizedBox(height: 10),
            Center(
              child: Text(
                settings.serverMock
                    ? 'Server mock pipeline ON · offline demo verdicts'
                    : 'Live OCR + Gemini · ${_hostOf(settings.baseUrl)}',
                style: LabelJaanoTheme.readout(size: 11, color: Palette.faint),
              ),
            ),
          ],
        ),
        if (_busy) _ScanningOverlay(mock: settings.serverMock),
      ],
    );
  }

  Widget _options(Settings settings) {
    return Container(
      clipBehavior: Clip.antiAlias,
      decoration: BoxDecoration(
        color: Palette.card,
        borderRadius: BorderRadius.circular(16),
        border: const Border.fromBorderSide(BorderSide(color: Palette.hairline)),
      ),
      child: Theme(
        data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
        child: ExpansionTile(
          tilePadding: const EdgeInsets.symmetric(horizontal: 16),
          childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
          leading: const Icon(Icons.tune_rounded, color: Palette.brassDeep),
          title: Text('Scan options',
              style: LabelJaanoTheme.display(size: 15, weight: FontWeight.w600)),
          subtitle: Text('Category · calibration · pipeline',
              style: Theme.of(context).textTheme.bodyMedium?.copyWith(fontSize: 12)),
          children: [
            DropdownButtonFormField<String>(
              value: _category,
              isExpanded: true,
              decoration: const InputDecoration(labelText: 'Category'),
              items: [
                for (final c in AppInfo.categories)
                  DropdownMenuItem(value: c.id, child: Text(c.label)),
              ],
              onChanged: (v) => setState(() => _category = v ?? ''),
            ),
            const SizedBox(height: 4),
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              activeColor: Palette.brass,
              value: _useSampleCalibration,
              onChanged: (v) => setState(() => _useSampleCalibration = v),
              title: Text('Sample-label calibration',
                  style: LabelJaanoTheme.display(size: 14, weight: FontWeight.w600)),
              subtitle: Text(
                'Measure Rule 8 font heights on the bundled demo labels. Leave off '
                'for real photos (no reference ⇒ font-height is skipped, not failed).',
                style: Theme.of(context).textTheme.bodyMedium?.copyWith(fontSize: 12),
              ),
            ),
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              activeColor: Palette.brass,
              value: settings.serverMock,
              onChanged: (v) => context.read<Settings>().serverMock = v,
              title: Text('Use server mock pipeline',
                  style: LabelJaanoTheme.display(size: 14, weight: FontWeight.w600)),
              subtitle: Text(
                'Ask the backend to skip live OCR/Gemini. Handy before the heavy '
                'deps or API key are set up.',
                style: Theme.of(context).textTheme.bodyMedium?.copyWith(fontSize: 12),
              ),
            ),
            TextField(
              controller: _note,
              decoration: const InputDecoration(
                labelText: 'Note (optional)',
                hintText: 'Shop name / location',
              ),
            ),
          ],
        ),
      ),
    );
  }

  String _hostOf(String url) {
    final u = Uri.tryParse(url.startsWith('http') ? url : 'http://$url');
    if (u == null) return url;
    return u.host + (u.hasPort ? ':${u.port}' : '');
  }
}

class _CaptureSlot extends StatelessWidget {
  const _CaptureSlot({
    required this.label,
    required this.hint,
    required this.picked,
    required this.required,
    required this.onTap,
    required this.onClear,
  });

  final String label;
  final String hint;
  final Uint8List? picked;
  final bool required;
  final VoidCallback onTap;
  final VoidCallback? onClear;

  @override
  Widget build(BuildContext context) {
    final hasImage = picked != null;
    return GestureDetector(
      onTap: onTap,
      child: Container(
        height: 116,
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(
          color: hasImage ? Palette.card : Palette.brassTint.withOpacity(0.35),
          borderRadius: BorderRadius.circular(16),
          border: Border.all(
            color: hasImage ? Palette.hairline : Palette.brass.withOpacity(0.5),
            width: hasImage ? 1 : 1.4,
          ),
        ),
        child: Row(
          children: [
            ClipRRect(
              borderRadius: BorderRadius.circular(12),
              child: SizedBox(
                width: 92,
                height: 92,
                child: hasImage
                    ? Image.memory(picked!, fit: BoxFit.cover)
                    : Container(
                        color: Palette.card,
                        child: const Icon(Icons.add_a_photo_outlined,
                            color: Palette.brassDeep, size: 26),
                      ),
              ),
            ),
            const SizedBox(width: 14),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Row(
                    children: [
                      Flexible(
                        child: Text(label,
                            style: LabelJaanoTheme.display(
                                size: 15, weight: FontWeight.w600)),
                      ),
                      if (required) ...[
                        const SizedBox(width: 6),
                        Text('required', style: LabelJaanoTheme.eyebrow(color: Palette.brassDeep)),
                      ],
                    ],
                  ),
                  const SizedBox(height: 4),
                  Text(hasImage ? 'Tap to replace' : hint,
                      style: Theme.of(context).textTheme.bodyMedium?.copyWith(fontSize: 12.5)),
                ],
              ),
            ),
            if (hasImage && onClear != null)
              IconButton(
                icon: const Icon(Icons.close_rounded, color: Palette.muted),
                onPressed: onClear,
              )
            else
              const Icon(Icons.chevron_right_rounded, color: Palette.faint),
          ],
        ),
      ),
    );
  }
}

class _ScanningOverlay extends StatelessWidget {
  const _ScanningOverlay({required this.mock});
  final bool mock;

  @override
  Widget build(BuildContext context) {
    return Positioned.fill(
      child: Container(
        color: Palette.navy.withOpacity(0.72),
        child: Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const _Caliper(),
              const SizedBox(height: 22),
              Text('Reading the label…',
                  style: LabelJaanoTheme.display(
                      size: 18, weight: FontWeight.w600, color: Colors.white)),
              const SizedBox(height: 8),
              Text(
                mock
                    ? 'Offline mock pipeline'
                    : 'OCR · calibration · Gemini · rule engine',
                style: LabelJaanoTheme.readout(size: 12, color: Colors.white70),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// A small animated caliper used on the scanning overlay.
class _Caliper extends StatefulWidget {
  const _Caliper();
  @override
  State<_Caliper> createState() => _CaliperState();
}

class _CaliperState extends State<_Caliper> with SingleTickerProviderStateMixin {
  late final AnimationController _c =
      AnimationController(vsync: this, duration: const Duration(milliseconds: 1400))
        ..repeat(reverse: true);

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: 120,
      height: 56,
      child: AnimatedBuilder(
        animation: _c,
        builder: (context, _) => CustomPaint(painter: _CaliperScanPainter(_c.value)),
      ),
    );
  }
}

class _CaliperScanPainter extends CustomPainter {
  _CaliperScanPainter(this.t);
  final double t;

  @override
  void paint(Canvas canvas, Size size) {
    final beam = Paint()
      ..color = Colors.white54
      ..strokeWidth = 2
      ..strokeCap = StrokeCap.round;
    // Graduated beam
    canvas.drawLine(Offset(0, 10), Offset(size.width, 10), beam);
    for (double x = 0; x <= size.width; x += 10) {
      canvas.drawLine(Offset(x, 10), Offset(x, x % 50 == 0 ? 22 : 16), beam);
    }
    // Sliding brass jaw
    final jawX = 8 + t * (size.width - 16);
    final jaw = Paint()
      ..color = Palette.brass
      ..strokeWidth = 3
      ..strokeCap = StrokeCap.round;
    canvas.drawLine(Offset(jawX, 4), Offset(jawX, size.height - 6), jaw);
    canvas.drawCircle(Offset(jawX, size.height - 6), 4, Paint()..color = Palette.brass);
  }

  @override
  bool shouldRepaint(covariant _CaliperScanPainter old) => old.t != t;
}
