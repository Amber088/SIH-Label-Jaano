# Turning on real scanning (Gemini) — and rejecting non-labels

By default the app can run in **mock mode**, which returns the same canned
"compliant Glucose Biscuits" read for *any* image — that is why a photo of a
book came back as compliant. This guide switches the backend to a **real read**
so the camera actually drives the verdict, and a book (or any non-label) is
reported as **"No label detected"** instead of a fake pass.

There are two independent switches, and you need **both**:

1. The backend must have a **Gemini API key** (for the real vision read).
2. The app's **"Server mock" toggle must be OFF** (Settings screen), so it asks
   the backend for a real read instead of the canned one.

---

## 1. Get a free Gemini API key (2 minutes)

1. Go to **https://aistudio.google.com/apikey** and sign in with a Google account.
2. Click **"Create API key"** (choose "Create in new project" if prompted).
3. Copy the key — it looks like `AIza...`. Keep it private; don't commit it.

The free tier is enough for demoing: `gemini-2.0-flash` allows a generous number
of requests per day at no cost.

---

## 2. Run the backend with the key

In the **same terminal** you use to start the server (the export only lives in
that shell):

```bash
cd ~/Desktop/SIH/LabelJaano/backend

# make the key available to the server process
export GEMINI_API_KEY="AIza...your key..."

# (first time only) make sure Gemini + image libs are installed
python3 -m pip install "google-generativeai>=0.7" "pillow>=10.0"

# start the API, reachable from your phone on the LAN
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Notes:
- `--host 0.0.0.0` is what lets the phone reach your Mac (you already use your
  Mac's LAN IP in the app's Settings → Base URL).
- You do **not** need numpy / PaddleOCR / OpenCV. They're only for the Rule 8
  *font-height* measurement, which simply **skips** when they're absent — the
  rest of the scan runs normally on Gemini. (The backend degrades gracefully;
  it will no longer 500 the way it did before.)
- Sanity check the key is seen: the server should start without warnings, and a
  scan with the mock toggle OFF should return a real, image-specific verdict.

---

## 3. Turn OFF "Server mock" in the app

Open the app → **Settings** → turn **"Server mock" OFF**.

- Mock **ON**  → canned compliant data, ignores the photo (useful only for UI demos).
- Mock **OFF** → real Gemini read of the actual photo.

If the toggle is OFF **and** the backend has **no** key, a scan returns a clear
**503** ("Gemini not configured") rather than a fake pass — the app shows a tip
to either set the key or turn mock back on. That's intentional: no silent fakes.

---

## What you'll see now

| You scan…                    | Verdict                                   |
|------------------------------|-------------------------------------------|
| A real packaged label        | `Compliant` / `Needs review` / `Non-compliant` with a score and the exact rule citations |
| A book cover / face / scenery| **`No label detected`** — a neutral screen explaining no product label was found (no score, no fake violations) |

"No label detected" reads are kept in the queue under **All**, but are **excluded**
from the dashboard's compliance rate and average score (they aren't real
inspections, just mis-aimed photos).

---

## How "No label detected" is decided (and its limits)

After the real read, the engine asks *"is this even a label?"* before scoring:

- If Gemini extracted **any** declaration value (net quantity, MRP, manufacturer,
  a date, …) → it's treated as a product and scored normally.
- If it extracted **nothing**, the raw text is checked for a commodity fingerprint
  (net qty / MRP / FSSAI / mfg / batch / a weight or volume like `500 g`, `1 L`).
  No field **and** no such signal → **No label detected**.

Known edge cases (acceptable, worth a one-line mention if a judge asks):
- An Indian **book that prints "M.R.P. ₹399"** on the back carries an MRP signal,
  so it is scored as a (usually non-compliant) product rather than "No label".
  This is deliberate — a printed MRP is exactly what Legal Metrology cares about.
- A blurry real label where Gemini reads nothing at all could fall through to
  "No label detected"; re-shoot closer / in better light.

---

## Quick reference

```bash
# real read
export GEMINI_API_KEY="AIza..."          # or GOOGLE_API_KEY
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
# …and turn the app's "Server mock" toggle OFF

# offline UI demo (no key, canned data)
# just leave the app's "Server mock" toggle ON
```
