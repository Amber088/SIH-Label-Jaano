# Sharing Label Jaano with other people

Two things have to travel together: the **app**, and a **backend it can reach**. The app
alone is inert — it holds no rules and does no extraction; every verdict comes from the
API. So "sharing the app" is really two jobs, and the second one is the awkward half.

Read this once before either route, because it explains most of what would otherwise
look like bugs: **the app persists nothing.** `Settings` keeps the base URL and the
"Use server mock pipeline" toggle in memory only, so both revert to their *compiled-in*
defaults on every cold start. Typing a URL on someone else's phone therefore lasts until
they close the app. That is why both routes below bake the address into the build with
`--dart-define` instead:

| Define | Effect |
|---|---|
| `LJ_BASE_URL=https://…` | The backend the app talks to on launch. Unset ⇒ the dev Mac's LAN IP. |
| `LJ_SERVER_MOCK=false` | Real optical reads on launch. Unset ⇒ **mock ON**, i.e. the same canned compliant label for every photo. |

Ship an APK without that second define and your reviewer will scan a book, see
"Compliant", and conclude the project is fake. It is the single most important flag here.

---

## Route A — share it today (temporary public URL, ~10 minutes)

Good for a mentor call, a remote teammate, or a quick "look at this". Costs nothing and
needs no account, but the URL dies when you close your laptop.

**1. Keep the backend running** in its own terminal:

```bash
~/dev/SIH/LabelJaano/run.sh
```

**2. Open a tunnel to it** in a second terminal:

```bash
brew install cloudflared          # once
cloudflared tunnel --url http://localhost:8000
```

It prints a URL like `https://damp-forest-1234.trycloudflare.com`. Confirm it works from
outside your network before you hand it to anyone:

```bash
curl -s https://damp-forest-1234.trycloudflare.com/health     # expect packs_loaded: 8
```

**3. Build an APK that points at it** (third terminal):

```bash
cd ~/dev/SIH/LabelJaano/mobile
flutter build apk --release \
  --dart-define=LJ_BASE_URL=https://damp-forest-1234.trycloudflare.com \
  --dart-define=LJ_SERVER_MOCK=false
```

The file lands at `build/app/outputs/flutter-apk/app-release.apk`, roughly 20–40 MB —
small enough for WhatsApp or Drive. The recipient taps it and allows "install from
unknown sources" when Android asks; no Play Store, no developer mode, no cable.

**The catch, stated plainly:** every `cloudflared` restart mints a *new* hostname, and
the old APK keeps pointing at the dead one. Fine for an afternoon, wrong for anything you
print on a slide.

---

## Route B — a permanent URL (Render free tier, ~45 minutes once)

This is the version that survives your laptop closing, and the one to put in a
submission. `render.yaml` at the repo root already describes the service.

**1. Push your work.** You have uncommitted changes and a commit that never left the
machine. Look at the list before committing — `-A` sweeps in the idea deck and the new
docs too, which is fine, but check nothing unexpected is there:

```bash
cd ~/dev/SIH
git status
git add -A && git commit -m "Add sharing setup: render blueprint, build-time base URL"
git push
```

(`backend/.env` cannot be caught by this — it is gitignored, and I re-checked. Same for
`.claude/`.)

**2. Deploy.** On [render.com](https://render.com): **New → Blueprint**, choose
`SIH-Label-Jaano`, apply. Render reads `render.yaml`, builds `backend/Dockerfile` with
`LabelJaano/` as the context, and gives you `https://labeljaano-api.onrender.com`.

**3. Set the key.** Service → **Environment** → set `GEMINI_API_KEY` to the same value
that is in `backend/.env`. `LABEL_JAANO_SECRET` is generated for you; the officer code is
optional and better left unset on a public URL.

**4. Verify it is doing real reads — do not skip this.** In the service's **Logs**, look
for:

```
extraction mode=LIVE — live OCR and live vision model
```

If it says `mode=MOCK`, the key did not arrive, and every verdict the service returns is
canned. Also `curl https://labeljaano-api.onrender.com/health` and expect
`packs_loaded: 8`.

You have a second net here, which is worth knowing about: if the app asked for a live read
and the server quietly served a mock one anyway, the scan screen raises "Extraction fell
back to the offline mock" with the server's reason. So a mis-deployed backend announces
itself instead of handing a stranger a fake "Compliant".

**5. Build the shareable APK** against it:

```bash
cd ~/dev/SIH/LabelJaano/mobile
flutter build apk --release \
  --dart-define=LJ_BASE_URL=https://labeljaano-api.onrender.com \
  --dart-define=LJ_SERVER_MOCK=false
```

Send that APK to anyone, on any network. To confirm it carries the right address, open
the installed app's **Settings** tab — the base URL field shows what was compiled in.

---

## Limits worth knowing before someone else finds them

**The free instance sleeps.** After ~15 minutes idle, Render stops the container; the
next request wakes it and takes roughly 30–60 seconds, during which the app looks broken.
Before any demo, load `/health` once to wake it, then scan. This is the most likely way a
live demo embarrasses you.

**History is ephemeral.** The free plan has no persistent disk, so the SQLite file inside
the container — accounts, saved inspections — is wiped by every redeploy or restart.
Scanning is unaffected. Mount a paid disk at `/data` if it ever needs to last.

**Anyone with the URL can spend your Gemini quota.** Scanning deliberately needs no
account, so the endpoint is open. Share the URL with people, not with the internet, and
rotate the key in AI Studio once the event is over.

**Font-height checks stay skipped.** The image ships without the OCR stack (paddleocr,
paddlepaddle, opencv — hundreds of megabytes and a fragile build), and OCR is the only
source of pixel geometry for the Rule 8 letter-height rule. Those checks report **skipped**,
never "passed". Everything else in a scan is a real read. Say this before a judge asks;
it reads as rigour, whereas being caught by it reads as a gap.

**Cleartext HTTP is not a problem.** Both routes give you `https://` anyway, and even the
LAN `http://` case works: Flutter's `dart:io` sockets are not governed by Android's
`NetworkSecurityPolicy`, so no manifest flag is involved.
