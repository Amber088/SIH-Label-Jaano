import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:provider/provider.dart';

import '../core/config.dart';
import '../core/theme.dart';
import '../models/scan_record.dart';
import '../services/api_client.dart';
import '../services/scan_store.dart';
import '../services/session.dart';
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
  final ImagePicker _picker = ImagePicker();
  final TextEditingController _note = TextEditingController();
  final TextEditingController _product = TextEditingController();
  final TextEditingController _location = TextEditingController();

  _Picked? _front;
  _Picked? _back;
  String _category = '';
  bool _useSampleCalibration = false;

  /// Whether to file this inspection on the server. Only meaningful when signed
  /// in; an officer occasionally wants a verdict without adding a row to the
  /// record, e.g. re-scanning the same package to check a retake.
  bool _file = true;
  bool _busy = false;

  @override
  void dispose() {
    // The HTTP client belongs to the provider graph, which disposes it — closing
    // it here would break every other screen that shares it.
    _note.dispose();
    _product.dispose();
    _location.dispose();
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
    final session = context.read<Session>();
    final api = context.read<ApiClient>();

    setState(() => _busy = true);
    try {
      final images = <LabelImage>[
        LabelImage(_front!.bytes, _front!.filename),
        if (_back != null) LabelImage(_back!.bytes, _back!.filename),
      ];
      final outcome = await api.scanImage(
        baseUrl: settings.baseUrl,
        images: images,
        // Anonymous scanning is a supported mode, not a degraded one: a null
        // token gets the same verdict, the server just files nothing.
        token: session.token,
        category: _category,
        serverMock: settings.serverMock,
        reference: _useSampleCalibration ? kSampleCalibration : null,
        productName: _product.text,
        note: _note.text,
        location: _location.text,
        save: session.isSignedIn ? _file : null,
      );

      final record = ScanRecord(
        // When the server filed it, adopt its id so the local copy and the
        // server row are the same inspection everywhere downstream — otherwise
        // the next sync would show the scan twice.
        id: outcome.scanId ?? DateTime.now().microsecondsSinceEpoch.toString(),
        capturedAt: DateTime.now(),
        report: outcome.report,
        thumbnails: [_front!.bytes, if (_back != null) _back!.bytes],
        note: _trimmedOrNull(_note.text),
        // What the *server* actually did, not what we asked for. It may fall back
        // to the mock on its own (no API key, deps missing), and a canned verdict
        // must never be presented as a live read of this photo.
        serverMock: outcome.extractionMock,
        serverId: outcome.scanId,
        productName: _trimmedOrNull(_product.text),
        location: _trimmedOrNull(_location.text),
        ownerId: session.account?.id,
      );
      store.add(record);

      if (!mounted) return;
      // Asked for a live read and got a canned one: say so now, while the photo
      // is still in mind, rather than letting the verdict stand unqualified.
      if (outcome.extractionMock && !settings.serverMock) {
        _toast('Extraction fell back to the offline mock — '
            '${outcome.extractionReason.isEmpty ? "see the report" : outcome.extractionReason}');
      } else if (session.isSignedIn && _file && !outcome.saved) {
        _toast('Verdict ready, but the server did not file it — '
            'it stays on this device only.');
      }
      // Reset the form for the next package.
      setState(() {
        _front = null;
        _back = null;
        _note.clear();
        _product.clear();
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

  /// Location is deliberately *not* cleared between scans: an inspector works a
  /// shop at a time, and retyping the same shop for every package is the kind of
  /// friction that ends with the field left empty.
  static String? _trimmedOrNull(String raw) {
    final v = raw.trim();
    return v.isEmpty ? null : v;
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
    final session = context.watch<Session>();

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
            _options(settings, session),
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
            const SizedBox(height: 4),
            Center(
              child: Text(
                _filingLine(session),
                textAlign: TextAlign.center,
                style: LabelJaanoTheme.readout(size: 11, color: Palette.faint),
              ),
            ),
          ],
        ),
        if (_busy) _ScanningOverlay(mock: settings.serverMock),
      ],
    );
  }

  /// Say plainly where this scan is going to end up. An inspection nobody can
  /// retrieve later is worth less than one that is filed, and the difference
  /// should not be a surprise discovered on the queue tab.
  String _filingLine(Session session) {
    if (!session.isSignedIn) {
      return 'Not signed in · verdict only, kept on this device until you close the app';
    }
    if (!_file) return 'Filing off · this scan will not be added to your history';
    return 'Will be filed to your history as ${session.account?.displayName ?? "you"}';
  }

  Widget _options(Settings settings, Session session) {
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
            // Only offered when signed in: with no account there is nothing to file
            // to, and a switch that cannot change the outcome is worse than absent.
            if (session.isSignedIn)
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                activeColor: Palette.brass,
                value: _file,
                onChanged: (v) => setState(() => _file = v),
                title: Text('File to my inspection history',
                    style: LabelJaanoTheme.display(size: 14, weight: FontWeight.w600)),
                subtitle: Text(
                  'Off gives you the verdict without adding a row to the record — '
                  'useful when re-shooting the same package.',
                  style: Theme.of(context).textTheme.bodyMedium?.copyWith(fontSize: 12),
                ),
              ),
            const SizedBox(height: 4),
            TextField(
              controller: _product,
              textCapitalization: TextCapitalization.words,
              decoration: const InputDecoration(
                labelText: 'Product (optional)',
                hintText: 'What is on the label — heads the history row',
              ),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _location,
              textCapitalization: TextCapitalization.words,
              decoration: const InputDecoration(
                labelText: 'Place of inspection (optional)',
                hintText: 'Shop / market — kept between scans',
              ),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _note,
              maxLines: 2,
              decoration: const InputDecoration(
                labelText: 'Note (optional)',
                hintText: 'Remarks recorded with the inspection',
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
