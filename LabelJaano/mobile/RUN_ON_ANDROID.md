# Run Label Jaano on a physical Android phone

Follow these phases in order on your **Mac's terminal**. Paste any error back to
Claude and it'll debug the exact output. Each phase ends with a ✅ check — don't
move on until it passes.

> **What "working" looks like today:** the backend runs in **mock mode** (no API
> key, no heavy ML deps). Because the app uploads photo *bytes*, mock mode returns
> a built-in **compliant** food-label report for every scan — so this proves the
> full photo → verdict → report flow end-to-end, but the verdict won't vary with
> the photo. Real per-photo verdicts need a `GEMINI_API_KEY` (see Phase 6).

---

## Phase 0 — Check the Flutter toolchain

```bash
flutter --version          # must be 3.24.0 or newer
flutter doctor -v
```

- If `flutter` isn't found → install it: `brew install --cask flutter` (or the
  manual SDK from docs.flutter.dev), then reopen the terminal.
- In `flutter doctor`, you need a green check on **Flutter** and **Android
  toolchain**. If Android shows "cmdline-tools missing" or "licenses not
  accepted", run:

```bash
flutter doctor --android-licenses     # press y to accept all
```

- The **Xcode** / **Chrome** lines can stay red — you don't need them for Android.

✅ **Check:** `flutter --version` ≥ 3.24 and `flutter doctor` shows Flutter +
Android toolchain OK. *(Paste `flutter doctor -v` output to Claude if unsure.)*

---

## Phase 1 — Start the backend (mock mode, 4 light packages only)

```bash
cd /Users/amberjain/Desktop/SIH/LabelJaano/backend
python3 -m venv .venv
source .venv/bin/activate
pip install "fastapi>=0.110" "uvicorn[standard]>=0.29" "pydantic>=2.6" "python-multipart>=0.0.9"
```

> Do **not** `pip install -r requirements.txt` yet — that pulls `paddlepaddle`,
> which is slow/fragile on Apple Silicon. The four packages above are all the
> mock demo needs.

Start it, bound so your phone can reach it, with mock mode on:

```bash
LABEL_JAANO_MOCK=1 uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Leave this terminal running. In a **second** terminal, confirm it's alive:

```bash
curl http://localhost:8000/health
# {"status":"ok",...,"packs_loaded":8,...}
```

✅ **Check:** `/health` returns JSON with `packs_loaded: 8` — one per file in
`rulepacks/`. A given scan is judged by the subset its category selects (six of the
eight for `packaged_food`), which is what `/categories` reports as `packs`.

---

## Phase 2 — Find your Mac's LAN IP + clear the firewall

The phone can't use `localhost`; it needs your Mac's address on the Wi-Fi.

```bash
ipconfig getifaddr en0 || ipconfig getifaddr en1
# e.g. 192.168.1.42
```

Note that number — call it `<MAC_IP>`. Both the phone and Mac must be on the
**same Wi-Fi network**.

If macOS's firewall is on, the first incoming connection will prompt "Do you want
the application 'Python' to accept incoming connections?" → click **Allow**.
(System Settings → Network → Firewall → Options, if you need to add it manually.)

✅ **Check:** from the Mac, `curl http://<MAC_IP>:8000/health` also works (not
just localhost).

---

## Phase 3 — Put the phone in developer/USB-debugging mode

On the Android phone:

1. **Settings → About phone** → tap **Build number** 7 times until it says
   "You are now a developer."
2. **Settings → System → Developer options** → turn on **USB debugging**.
3. Plug the phone into the Mac with a USB cable (a data cable, not charge-only).
4. On the phone, tap **Allow** on the "Allow USB debugging?" prompt (tick "always
   allow from this computer").

Then, back on the Mac:

```bash
flutter devices
```

Your phone should appear in the list (by model name).

✅ **Check:** `flutter devices` lists your phone.

---

## Phase 4 — Build and launch the app

```bash
cd /Users/amberjain/Desktop/SIH/LabelJaano/mobile
flutter pub get
flutter run               # if prompted, choose your phone
```

> `android/` is already committed, so **do not** run `flutter create .` — it is only
> for a checkout that has `lib/` and `pubspec.yaml` but no platform folders. Here it
> would rewrite `android/app/src/main/AndroidManifest.xml` over the top of the app
> label and the `<queries>` block the image picker needs.

- First run triggers a **Gradle build** that downloads Android build deps — it can
  take a few minutes and needs internet. Later runs are fast.
- If `flutter pub get` complains about version resolution, run `flutter pub upgrade`.

✅ **Check:** the app installs and opens on the phone showing the **Dashboard** tab
(with an empty state prompting you to scan).

---

## Phase 5 — Point the app at your Mac and scan

In the app:

1. Go to the **Settings** tab → **Backend connection**.
2. Set **API base URL** to `http://<MAC_IP>:8000` (the IP from Phase 2).
3. Tap **Test connection** → it should say **"Connected · 2 rule pack(s) loaded."**
4. Confirm **"Use server mock pipeline"** is **ON** (Settings → Extraction pipeline).
5. Go to the **Scan** tab → tap **Front panel (PDP)** → *Take a photo* or *Choose
   from gallery* (any label; in mock mode the result is the same canned compliant
   report). → **Run compliance scan**.
6. The **report** opens: verdict + score gauge, pass/fail/skip tally, and each
   declaration checked with its rule reference. Back out to **Dashboard** and
   **Queue** to see the aggregates fill in.

✅ **Check:** you get a compliance report on the phone. 🎉 The app is running.

---

## Phase 6 — (Optional) Real per-photo verdicts

To make the verdict reflect the actual photo (and show real violations), give the
backend a Gemini key instead of mock mode. Stop uvicorn, then:

```bash
pip install "google-genai>=1.0" pillow
export GEMINI_API_KEY=your_key_here
uvicorn app.main:app --host 0.0.0.0 --port 8000     # note: no LABEL_JAANO_MOCK
```

In the app's Settings, turn **"Use server mock pipeline" OFF**. Now each scan does
a live structured read of the photo. *(Font-height / Rule 8 checks also need
OCR — `paddleocr`+`paddlepaddle` — otherwise they're skipped, not failed. Ask
Claude before installing those on Apple Silicon.)*

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| App: "Connection refused" / timeout on Test connection | Wrong IP, phone not on same Wi-Fi, backend not started with `--host 0.0.0.0`, or firewall blocked Python. Re-check Phase 2. |
| `flutter devices` doesn't list the phone | Charge-only cable, USB debugging off, or you didn't tap "Allow" on the phone. Re-plug and watch for the prompt. |
| Gradle build fails / hangs | Needs internet on first build. Retry `flutter run`; if it mentions a licence, run `flutter doctor --android-licenses`. |
| `flutter pub get` version conflict | `flutter pub upgrade`. |
| Camera does nothing | Grant the camera permission when Android prompts on first capture. |
| Verdict is always "compliant" | Expected in mock mode (see the note at the top). Do Phase 6 for real verdicts. |
