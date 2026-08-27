# Label Jaano — mobile app

A field-officer companion for the **Legal Metrology (Packaged Commodities) Rules, 2011**.
Photograph a package, and the app calls the FastAPI backend to run OCR → font
calibration → Gemini extraction → the rule engine, then renders a defensible
verdict where **every violation cites its exact rule**.

Built with Flutter (Material 3). This directory holds `pubspec.yaml` and `lib/`
only — the platform folders (`android/`, `ios/`, `web/`) are generated locally in
one command (see below), so they aren't checked in.

---

## What's inside

```
lib/
  main.dart                 App root: providers + MaterialApp + theme
  core/
    config.dart             Default base URL per platform, app metadata, categories
    theme.dart              "Measurement instrument" identity: Palette + LabelJaanoTheme
  models/
    compliance_report.dart  Mirrors the backend ComplianceReport.to_dict()
    scan_record.dart        One in-session inspection (report + thumbnails + note)
  services/
    api_client.dart         Multipart POST /scan/image, /health, /rulepacks
    settings.dart           Base URL + server-mock toggle (ChangeNotifier)
    scan_store.dart         In-memory history + dashboard/queue aggregates
  widgets/                  Brand motif, score gauge, charts, tiles, common bits
  screens/
    home_shell.dart         4-tab shell: Dashboard · Scan · Queue · Settings
    dashboard_screen.dart   Summary stats, verdict donut, severity bar, recent
    scan_screen.dart        Capture front/back → options → run scan
    report_screen.dart      Verdict banner, tallies, violations, all checks
    queue_screen.dart       Officer queue, filterable by verdict
    settings_screen.dart    Connection, health check, rule-pack browser, session
```

State lives in memory for the session (via `provider`). There is no persistence
or analytics endpoint yet — the dashboard and queue are faithful client-side
projections of the scans you've run since launch.

---

## Prerequisites

- **Flutter 3.24 or newer** (Dart 3.5+). Check with `flutter --version`.
  The lower bound is set by the Material 3 theming APIs the app uses
  (`WidgetStateProperty`, the `CardThemeData` form of `ThemeData.cardTheme`).
- The **Label Jaano backend** running and reachable (see below).
- A device or emulator: Android emulator, iOS simulator (macOS), or a physical
  phone on the same Wi-Fi/LAN as the machine running the backend.

---

## 1. Start the backend

From the repository root:

```bash
cd backend
python -m venv .venv && source .venv/bin/activate      # first time only
pip install -r requirements.txt                        # first time only
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

`--host 0.0.0.0` matters: it lets the Android emulator and a physical phone
reach the server, not just `localhost`. Confirm it's up:

```bash
curl http://localhost:8000/health
# {"status":"ok","version":"...","packs_loaded":2,"pack_ids":[...]}
```

> **No API key or heavy deps yet?** That's fine for the demo — leave the app's
> **"Use server mock pipeline"** switch ON (it is by default). The backend then
> returns verdicts from its offline mock OCR + Gemini path, so you get a full
> end-to-end run with nothing extra installed. Turn it OFF once `paddleocr`,
> `google-generativeai`, and `GEMINI_API_KEY` are configured on the server for
> genuine on-photo extraction.

---

## 2. Run the app

From this `mobile/` directory:

```bash
flutter create .          # generates android/ ios/ web/ etc. (first time only)
flutter pub get
flutter run               # pick your device when prompted
```

`flutter create .` keeps the existing `pubspec.yaml` and `lib/`; it only adds the
missing platform scaffolding. If dependency resolution ever complains on a very
new Flutter, `flutter pub upgrade` will nudge the pinned packages forward.

---

## 3. Point the app at your backend

The app guesses a sensible base URL per platform on first launch, but you can
change it any time in **Settings → Backend connection** and hit **Test
connection** to verify (it reports how many rule packs loaded).

| Where the app runs | Base URL to use | Why |
|---|---|---|
| **Android emulator** | `http://10.0.2.2:8000` | `10.0.2.2` is the emulator's alias for your host machine's `localhost`. |
| **iOS simulator** | `http://localhost:8000` | The simulator shares the Mac's network stack. |
| **Physical phone** | `http://<your-computer-LAN-IP>:8000` | e.g. `http://192.168.1.42:8000`. Find it with `ipconfig getifaddr en0` (macOS) or `hostname -I` (Linux). Phone and computer must be on the same Wi-Fi, and the backend must run with `--host 0.0.0.0`. |

---

## Platform permissions (physical devices)

`image_picker` needs camera/photo-library usage strings. `flutter create`
provides defaults, but for real-device camera capture confirm these:

**iOS** — `ios/Runner/Info.plist`:

```xml
<key>NSCameraUsageDescription</key>
<string>Label Jaano uses the camera to photograph package labels for compliance checks.</string>
<key>NSPhotoLibraryUsageDescription</key>
<string>Label Jaano reads label photos from your library to check compliance.</string>
```

**Android** — camera + gallery work out of the box with `image_picker`; no extra
manifest entries are required for the picker itself. If you later add direct
camera plugins, add `<uses-permission android:name="android.permission.CAMERA"/>`.

> **Fonts:** the instrument-panel typography (Space Grotesk / Inter / JetBrains
> Mono) is fetched and cached at first run via `google_fonts`, so the first
> launch wants a network connection. To ship fully offline, bundle the `.ttf`
> files as assets instead.

---

## Demo flow (for judges)

1. Backend running with the mock pipeline (default) — no keys needed.
2. Open the app → **Scan** tab.
3. **Front panel** → *Choose from gallery* → pick `backend/samples/label_front.png`
   (pull the sample onto the device/emulator first, or just shoot any label).
   Optionally add `label_back.png` as the back panel.
4. Expand **Scan options** and turn on **Sample-label calibration** — this feeds
   a known mm-per-pixel + principal-display-panel box so **Rule 8 font-height**
   checks are actually *measured* on the bundled samples. (For an arbitrary
   photo with no reference in frame, the engine *skips* font-height rather than
   guessing — so it never fails a label on a measurement it couldn't take.)
5. **Run compliance scan** → the report opens: verdict + score gauge, the
   pass/fail/skip tally, each violation with its **legal reference**, and the
   full check list.
6. Back out to **Dashboard** (verdict mix, severity breakdown, averages) and
   **Queue** (every inspection, filterable by verdict) to show the supervisor view.

Run `label_front.png` (compliant-ish) and a deliberately bad label back to back
to show the verdict swing from **Compliant** to **Non-compliant**.

---

## Backend contract (reference)

- `POST /scan/image` — multipart; repeatable field **`images`**; optional form
  fields `reference` (JSON), `context` (JSON), `category`, `mock`. Returns
  `ComplianceReport.to_dict()`.
- `GET /health` — `{status, version, packs_loaded, pack_ids}`.
- `GET /rulepacks` — list of `{pack_id, label, authority, version, scope,
  applies_when, declarations}`.

Verdicts: `compliant` · `needs_review` · `non_compliant`.
Severities: `critical` · `major` · `minor`. Outcomes: `pass` · `fail` · `skip`.

---

## Troubleshooting

- **"Connection refused" / timeout** → wrong base URL for your platform (see the
  table), or the backend isn't on `0.0.0.0`. Use **Settings → Test connection**.
- **503 from the server** → the live pipeline's deps/key aren't set up. Turn
  **server mock** back ON, or install `paddleocr` + `google-generativeai` and set
  `GEMINI_API_KEY`.
- **Build fails on an old Flutter** → upgrade to 3.24+ (`flutter upgrade`).
- **Fonts look like fallback** → first launch had no network; reconnect and
  restart, or bundle the fonts as assets.
