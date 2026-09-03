# LabelJaano — run commands

macOS / zsh. Paths are relative to the repo root `~/dev/SIH/LabelJaano`.

---

## 0. Zero-install: the rule engine alone

The engine has no dependencies — pure stdlib. This works on a machine with nothing
set up, which makes it the safest thing to run in front of a judge.

```bash
cd ~/dev/SIH/LabelJaano/backend

python3 run_scan.py samples/good_label.json     # COMPLIANT, 100.0/100, 35 checks
python3 run_scan.py samples/bad_label.json      # NON-COMPLIANT, 64.8/100, 11 failed
python3 run_scan.py samples/bad_label.json --json   # machine-readable
```

### The pack-override demo

Same label, two categories. The applied packs and the enforced rules change with no
code path involved.

```bash
cd ~/dev/SIH/LabelJaano/backend
python3 - <<'EOF'
import json
d = json.load(open('samples/good_label.json'))
d['category'] = 'wine'
json.dump(d, open('/tmp/wine_label.json', 'w'), indent=2)
EOF

python3 run_scan.py samples/good_label.json   # 6 packs, 35 checks, 100.0
python3 run_scan.py /tmp/wine_label.json      # 2 packs, 22 checks — alcoholic pack takes over
```

---

## 1. One-time backend setup

```bash
cd ~/dev/SIH/LabelJaano/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt      # API + tests, lean
# or: pip install -r requirements.txt    # adds paddleocr / paddlepaddle / opencv (heavy)
```

On Apple Silicon install `paddlepaddle` first from its own wheel index — see
`backend/README.md`. You do not need it for the demo; the mock path covers it.

---

## 2. Start the API

**Put your key in a file once, and stop typing it.** `backend/.env` is now read
automatically at startup — `app/main.py`, `extract.py` and `manage.py` all load it
before anything touches the environment. The file is gitignored, so the key never
reaches a commit.

```bash
cd ~/dev/SIH/LabelJaano/backend

# open backend/.env and fill in the three lines that are already waiting there:
#   GEMINI_API_KEY=AIza...            <- paste it bare: no quotes, no <angle brackets>
#   LABEL_JAANO_SECRET=...            <- from: .venv/bin/python manage.py secret
#   LABEL_JAANO_OFFICER_CODE=...      <- any passphrase; needed to enrol staff

.venv/bin/python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The startup log now tells you which of the two worlds you are in, so you can never
demo mock verdicts by accident:

```
extraction mode=LIVE — ...          # real Gemini read
extraction mode=MOCK — ...          # CANNED values, every check passes
```

A value exported in the shell still overrides the file, so this also works and takes
precedence:

```bash
export GEMINI_API_KEY="AIza..."     # no angle brackets
export LABEL_JAANO_SECRET="15fzz...your generated secret..."
```

The committed `.venv` was built when the repo lived at `~/Desktop/SIH`, so `activate` and
every console script in `.venv/bin` still point there. Calling the interpreter directly —
`.venv/bin/python -m uvicorn` — sidesteps both.

To repair the venv properly so `source .venv/bin/activate` works again — text scripts
only, symlinks and binaries skipped:

```bash
cd ~/dev/SIH/LabelJaano/backend
for f in .venv/bin/*; do
  [ -f "$f" ] && [ ! -L "$f" ] && grep -qI "Desktop/SIH" "$f" 2>/dev/null \
    && sed -i '' 's|/Users/amberjain/Desktop/SIH|/Users/amberjain/dev/SIH|g' "$f"
done
sed -i '' 's|Desktop/SIH|dev/SIH|g' .venv/pyvenv.cfg
```

Or rebuild it — it is gitignored and only 116 lean packages, no heavy OCR stack:

```bash
cd ~/dev/SIH/LabelJaano/backend
rm -rf .venv && python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pip install "google-genai>=1.0" "pillow>=10.0"     # real Gemini reads
```

Confirm:

```bash
curl http://localhost:8000/health
open http://localhost:8000/docs        # interactive Swagger UI
```

### Scan over HTTP

```bash
curl -s -X POST http://localhost:8000/scan \
  -H 'Content-Type: application/json' \
  -d @samples/bad_label.json | python3 -m json.tool | head -30

# photo in, verdict out (needs the extraction stack, or add -F mock=true)
curl -s -X POST http://localhost:8000/scan/image \
  -F 'images=@samples/label_front.png' \
  -F 'images=@samples/label_back.png' \
  -F 'category=packaged_food' | python3 -m json.tool | head -40

curl -s http://localhost:8000/rulepacks | python3 -m json.tool | head -20
```

---

## 3. Photo extraction from the CLI

```bash
cd ~/dev/SIH/LabelJaano/backend
source .venv/bin/activate
export GEMINI_API_KEY="AIza..."

python extract.py samples/label_front.png samples/label_back.png --evaluate

# with a reference object so font heights are measured in real millimetres
python extract.py front.jpg --evaluate \
  --reference '{"type":"card","width_mm":85.6,"bbox":[40,900,320,200]}'

# no key, no heavy deps — deterministic offline path
python extract.py samples/label_front.png --evaluate --mock
```

---

## 4. Tests

```bash
cd ~/dev/SIH/LabelJaano/backend
source .venv/bin/activate
pytest -q                      # all 308

pytest tests/test_engine.py -v
pytest -q -k "two_tier or auth"
```

Eleven test files also run standalone with no pytest and no install at all — useful on a
machine where nothing is set up:

```bash
python3 tests/test_engine.py
python3 tests/test_two_tier.py
python3 tests/test_auth.py
```

---

## 5. Admin CLI

```bash
cd ~/dev/SIH/LabelJaano/backend
source .venv/bin/activate

python manage.py init                            # create/upgrade the database
python manage.py secret                          # generate LABEL_JAANO_SECRET

export LABEL_JAANO_OFFICER_CODE="pick-something" # required to enrol staff
python manage.py createuser --role officer
python manage.py createuser --role admin         # admins can only be made here, by design

python manage.py seed --yes                      # demo corpus for the dashboard
python manage.py scans                           # recent inspections
python manage.py stats
python manage.py report <scan_id> -o report.html
python manage.py audit --since 7d
```

Admin-only reload, once you have a token:

```bash
curl -X POST http://localhost:8000/reload -H "Authorization: Bearer $TOKEN"
```

---

## 6. Phone demo, end to end

Your device from `flutter_run.log` is **V2059** (Vivo, Android). Two terminals.

### Terminal 1 — backend, reachable from the phone

```bash
cd ~/dev/SIH/LabelJaano/backend
source .venv/bin/activate

export LABEL_JAANO_SECRET="$(python manage.py secret)"
export GEMINI_API_KEY="<your key>"          # skip if already in ~/.zshrc

uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Find the address the phone must use, and prove it answers:

```bash
ipconfig getifaddr en0                       # e.g. 192.168.1.104
curl -s "http://$(ipconfig getifaddr en0):8000/health"
```

Open that same URL in Chrome **on the phone**. If it doesn't load, the phone will
never reach the API and no amount of Flutter debugging will help — the venue Wi-Fi is
usually the culprit. Jump to the USB route below.

### Terminal 2 — the app on the phone

```bash
cd ~/dev/SIH/LabelJaano/mobile
adb devices                                  # must say "device", not "unauthorized"
flutter devices                              # copy the id shown for V2059
flutter pub get
flutter run -d V2059
```

Then in the app: **Settings → Backend connection → API base URL** →
`http://192.168.1.104:8000` (substitute your actual IP), and turn
**"Use server mock pipeline" OFF** for a real Gemini read. It defaults ON.

### USB route — immune to Wi-Fi

Better for a judging venue, where client isolation often blocks phone-to-laptop traffic.

```bash
adb reverse tcp:8000 tcp:8000
```

The phone's `localhost:8000` now tunnels to your Mac over the cable. Set the app's API
base URL to `http://localhost:8000` and the LAN IP stops mattering. Re-run
`adb reverse` after every replug.

### If `flutter run` fails the way it did last time

Your log ends with `Error waiting for a debug connection: The log reader stopped
unexpectedly` / `Error launching application on V2059`. In order:

```bash
adb kill-server && adb start-server          # then unplug and replug
flutter run -d V2059 --verbose               # see where it actually dies
```

On Vivo specifically, Developer options needs **"USB debugging (Security settings)"**
and **"Install via USB"** enabled, and the app excluded from battery optimisation.

If it still won't attach, skip the debug connection entirely — the APK is already built
and the app runs fine standalone. You lose hot reload, nothing else:

```bash
adb install -r build/app/outputs/flutter-apk/app-debug.apk
# then tap the icon on the phone
```

### Other targets

```bash
flutter run -d chrome        # web, talks to localhost:8000
flutter run -d macos
```


---

## 7. Docker

```bash
cd ~/dev/SIH/LabelJaano        # build context is the repo root, not backend/

docker build -f backend/Dockerfile -t labeljaano .

docker run --rm -p 8000:8000 \
  -e LABEL_JAANO_SECRET="$(openssl rand -hex 32)" \
  -e GEMINI_API_KEY="$GEMINI_API_KEY" \
  -v labeljaano-data:/data \
  labeljaano
```

Runs as non-root uid 10001 with a healthcheck on `/health`.

---

## 8. Things that will bite you on demo day

**The Android base URL is hardcoded.** `mobile/lib/core/config.dart:15` returns
`http://192.168.1.104:8000` for Android. If your Mac's IP has changed, the phone gets
nothing. Check and override in the app's Settings screen:

```bash
ipconfig getifaddr en0
```

**The app's "Server mock" toggle defaults to ON** (`mobile/lib/services/settings.dart:23`).
A phone demo will show canned verdicts until you turn it off. Turn it off only once the
key and the extraction deps are actually on the server.

**`backend/.env` is read now — it did not used to be.** `backend/envfile.py` loads it at
import time in `app/main.py`, `extract.py` and `manage.py`. Three deliberate behaviours,
each one a trap that has already cost time here:

- a value exported in your shell **wins** over the file, so an old export can shadow a
  freshly pasted key — `echo "${GEMINI_API_KEY:0:6}..."` if a change seems ignored;
- blank and placeholder values in the file are **ignored**, so an unfilled line cannot
  wipe out a good shell export (plain `uvicorn --env-file .env` does exactly that);
- `<angle brackets>` around a value are stripped, because they get pasted along with keys
  copied out of documentation.

`uvicorn --env-file .env` also still works, but it has the clobbering behaviour above and
only affects the server — not `extract.py` or `manage.py`.

**No key means silent mock mode.** Docker without `-e GEMINI_API_KEY`, CI, or a
teammate's clone will return a confident clean verdict that means nothing. The startup
log now says `extraction mode=MOCK` when this happens — read it. Docker does not get
`.env` (it is gitignored and excluded from the build context), so the container still
needs `-e`. Verify before you present:

```bash
curl -s localhost:8000/health | python3 -m json.tool
echo "${GEMINI_API_KEY:0:6}..."      # confirm it is set in THIS shell
```

**Unset `LABEL_JAANO_SECRET` means an ephemeral key**, so every restart invalidates
every token. Fine for a single demo run, wrong for anything longer.

**`manage.py secret` only prints, it does not export.** `export LABEL_JAANO_SECRET="$(python
manage.py secret)"` captures the whole explanatory paragraph, not the secret. Run it,
then copy the value from the `export ...='...'` line it prints.

**`.venv` is a relocated venv.** It was created at `~/Desktop/SIH/...`; the repo now
lives at `~/dev/SIH/...`. `activate` exports a `VIRTUAL_ENV` that no longer exists and
`.venv/bin/uvicorn` has a dead shebang, so `uvicorn` falls through to anaconda's copy and
dies with `ModuleNotFoundError: No module named 'fastapi'`. `.venv/bin/python` is a
symlink to the real interpreter and still resolves site-packages correctly, which is why
`.venv/bin/python -m uvicorn` works. See section 2 to repair it.

**PaddleOCR is not installed** in that venv (no `paddleocr`, `paddlepaddle`, `opencv`,
`numpy`). Gemini extraction works — `google-genai` and `pillow` are there — but the
font-height check has no glyph geometry to measure, so it returns SKIP. That is the
designed behaviour, not a failure, and it is what the deck claims.

**A release APK has no network access.** `android/app/src/main/AndroidManifest.xml`
declares no `INTERNET` permission — it only appears in the `debug/` and `profile/`
manifests that Flutter generates. `flutter run` is unaffected, but `flutter build apk
--release` produces an app where every request fails. If you plan to hand an APK to
anyone, add this inside `<manifest>` in the main manifest:

```xml
<uses-permission android:name="android.permission.INTERNET"/>
```

**Impeller is disabled on purpose** (main manifest, `EnableImpeller` = false) because it
renders blank on some Vivo GPUs. The log warns that this opt-out is deprecated, so a
future Flutter upgrade may bring the blank screen back. Don't upgrade Flutter the week
of the demo.

**Version drift.** The committed `.venv` is Python 3.14.6 (anaconda); CI tests 3.11 and
3.12 and the Docker image is 3.12-slim. If something works locally and fails in CI,
start here.

---

## Verification note

Section 0, the pack-override demo, and the standalone test runners were executed and
produce the output shown. The uvicorn, Flutter, Docker and `manage.py` commands are
transcribed from the code and the project's own docs — I could not execute them, because
my sandbox has no PyPI access and the committed `.venv` is macOS-built. The device name
V2059 and the launch failure come from `LabelJaano/flutter_run.log`.




