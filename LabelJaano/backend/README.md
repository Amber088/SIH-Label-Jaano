# Label Jaano — Backend / Rule Engine

The compliance **rule engine** — the heart of the backend. It takes the normalized
output of the OCR + vision-LLM pipeline and judges it against the versioned JSON
rule packs in [`../rulepacks/`](../rulepacks/), producing a verdict, a 0–100 score,
and a list of violations that each cite their exact legal reference.

The **engine** is **pure Python standard library** — no dependencies to install
before you can run it. A thin **FastAPI layer** (`app/`) wraps it for HTTP; that layer
is the only part that needs `pip install`. So is everything added around it: accounts,
inspection history, and the print-ready report are all stdlib too (`sqlite3`, `hmac`,
`hashlib`, `html`), so the whole service still installs with nothing but FastAPI and
uvicorn. Only the OCR/vision pipeline has heavy optional deps, and they are imported
lazily — see `../ARCHITECTURE.md`.


## Layout

```
backend/
├── rule_engine/          # the engine — pure stdlib, zero deps
│   ├── models.py     # dataclasses: packs, scan input, results
│   ├── loader.py     # load rulepacks/*.json, select + merge by category
│   ├── checks.py     # validators, symbols, conditions, per-check evaluation
│   ├── engine.py     # evaluate(scan, ruleset) -> ComplianceReport
│   └── __init__.py   # public API
├── pipeline/            # photo -> scan-input: OCR + Gemini + calibration + fusion
│   ├── ocr.py        # PaddleOCR word boxes (real) / deterministic mock
│   ├── gemini.py     # Gemini 2.0 Flash structured field read (real) / mock
│   ├── calibration.py# pixel->mm scale (card / ArUco / manual) for Rule 8
│   ├── fusion.py     # merge the three signals into the scan-input contract
│   ├── prompts.py    # rule-driven extraction prompt (never drifts from packs)
│   ├── pipeline.py   # orchestrator: extract_scan_input / extract_and_evaluate
│   └── types.py      # pure-stdlib dataclasses shared across the pipeline
├── app/                  # the HTTP API — FastAPI wrapper around the engine
│   ├── schemas.py    # pydantic request/response models (Swagger docs)
│   ├── deps.py       # shared dependencies: current account, role gates, DB gate
│   ├── main.py       # meta + rulepack + scan endpoints
│   └── routers/
│       ├── auth_routes.py  # /auth/config /register /login /me /refresh
│       └── history.py      # /scans… /stats + the shareable report link
├── auth/                 # accounts and tokens — stdlib only
│   ├── passwords.py  # PBKDF2-HMAC-SHA256 hashing, constant-time verify
│   ├── tokens.py     # HS256 JWT-shaped session tokens (purpose "api")
│   ├── tickets.py    # short-lived share tickets (purpose "report"), scan-scoped
│   ├── roles.py      # consumer / officer / admin and what each may see
│   └── registration.py     # who may self-register as an officer (enrolment code)
├── store/                # persistence — sqlite3, no ORM
│   ├── db.py         # connection, schema, migrations, WAL, path resolution
│   ├── users.py      # account CRUD
│   └── scans.py      # filed inspections, filtered listing, corpus aggregates
├── reports/
│   └── inspection_html.py  # print-ready inspection report (stdlib templating)
├── samples/
│   ├── good_label.json   # a compliant packaged-food label (scan-input JSON)
│   ├── bad_label.json    # a deliberately non-compliant one
│   ├── label_front.png   # rendered demo photo (front panel) + .mock.json sidecar
│   ├── label_back.png    # rendered demo photo (back panel) + .mock.json sidecar
│   └── make_fixtures.py  # regenerates the demo photos + sidecars
├── tests/
│   ├── test_engine.py    # engine end-to-end + unit tests (no deps)
│   ├── test_two_tier.py  # scored checks vs lab-only reference standards
│   ├── test_pipeline.py  # pipeline end-to-end + unit tests (mock, no deps)
│   ├── test_store.py     # schema, migrations, listing, aggregates (no deps)
│   ├── test_auth.py      # hashing, tokens, tickets, roles (no deps)
│   ├── test_reports.py   # the HTML report renderer (no deps)
│   └── test_api.py       # API tests via TestClient
├── manage.py             # admin CLI: accounts, roles, history, reports, demo seed
├── extract.py            # CLI: photo(s) -> scan-input (and optionally a verdict)
└── run_scan.py           # CLI: scan-input JSON -> verdict
```


## Run it

```bash
cd backend

# judge the sample labels
python3 run_scan.py samples/good_label.json     # -> ✅ COMPLIANT, 100/100
python3 run_scan.py samples/bad_label.json      # -> ❌ NON-COMPLIANT, with violations
python3 run_scan.py samples/bad_label.json --json   # machine-readable report

# run the tests (either works)
python3 tests/test_engine.py
pytest
```

`run_scan.py` exits `0` compliant / `1` needs-review / `2` non-compliant, so it can
drop straight into CI or a batch pipeline.

## Run it as an HTTP API

The React dashboard, the Flutter app, and consumer mode all talk to one service.

```bash
cd backend
pip install -r requirements.txt          # fastapi, uvicorn, pydantic (+ httpx for tests)
# --reload-dir scopes the file-watcher to your source. Do NOT let it watch .venv:
# watching the virtualenv causes an endless reload loop (Python keeps writing cache
# files there, which the watcher reads as "changed"). Omit --reload to avoid it entirely.
uvicorn app.main:app --reload --reload-dir app --reload-dir rule_engine   # http://127.0.0.1:8000
```

Interactive Swagger docs (try requests in the browser): **http://127.0.0.1:8000/docs**

| Method | Path              | Auth | Purpose                                               |
|--------|-------------------|------|-------------------------------------------------------|
| GET    | `/health`         | —    | liveness, packs loaded, whether history is available  |
| GET    | `/rulepacks`      | —    | summary of every loaded rule pack                     |
| GET    | `/rulepacks/{id}` | —    | full raw JSON of one pack (audit what's enforced)     |
| POST   | `/scan`           | opt. | judge one normalized label (JSON) → `ComplianceReport`|
| POST   | `/extract`        | —    | label **photo(s)** → normalized scan-input JSON       |
| POST   | `/scan/image`     | opt. | label **photo(s)** → `ComplianceReport` (extract+judge)|
| POST   | `/reload`         | —    | re-read `rulepacks/` — hot-apply a gazette update     |
| GET    | `/auth/config`    | —    | what this server accepts at sign-up (draw the form)   |
| POST   | `/auth/register`  | —    | create an account → token (201)                       |
| POST   | `/auth/login`     | —    | email + password → token                              |
| GET    | `/auth/me`        | ✔    | the signed-in account                                 |
| POST   | `/auth/refresh`   | ✔    | slide an active session forward                       |
| GET    | `/scans`          | ✔    | inspection history, filtered + paged (scope by role)  |
| GET    | `/scans/{id}`     | ✔    | one filed inspection, report body included            |
| DELETE | `/scans/{id}`     | ✔    | delete one filed inspection (204)                     |
| POST   | `/scans/{id}/share` | ✔  | mint a short-lived link to this one report            |
| GET    | `/scans/{id}/report.html` | ticket | print-ready inspection report (opens in a browser) |
| GET    | `/stats`          | ✔    | corpus aggregates incl. corpus-wide `top_violations`  |

“Auth” above: **✔** needs a bearer session token, **opt.** works either way (with a
token the scan is filed and a `scan_id` comes back; without one you still get the
verdict and nothing is recorded), **ticket** takes the scoped ticket minted by
`/share` *instead of* a session token — see [Sharing a report](#sharing-a-report).


```bash
# health
curl -s http://127.0.0.1:8000/health

# judge a label (the samples are already in the right request shape)
curl -s -X POST http://127.0.0.1:8000/scan \
     -H "Content-Type: application/json" \
     -d @samples/good_label.json          # -> {"verdict":"compliant","score":100.0,...}

curl -s -X POST http://127.0.0.1:8000/scan \
     -H "Content-Type: application/json" \
     -d @samples/bad_label.json           # -> {"verdict":"non_compliant",...}

# after editing a JSON pack, hot-reload without restarting
curl -s -X POST http://127.0.0.1:8000/reload
```

Run the API tests (no server needed — they use an in-process test client):

```bash
pytest tests/test_api.py
python3 tests/test_api.py       # or standalone
```

CORS is open (`*`) for local development — tighten `allow_origins` in `app/main.py`
to your deployed frontend URLs before production.

## Scan from a photo (the extraction pipeline)

`/scan` and `run_scan.py` expect the *normalized* scan-input JSON. The **pipeline**
(`pipeline/`, `extract.py`, and the `/extract` + `/scan/image` endpoints) is what turns
an actual label **photo** into that JSON:

1. **OCR** (PaddleOCR) — word-level pixel boxes, so Rule 8 letter heights are real.
2. **Vision-LLM** (Gemini 2.0 Flash) — reads *what* each declaration says and *which*
   declaration it is. The prompt is built from the live rule packs, so the extractor
   can never drift from the rules.
3. **Calibration** — a known-size reference in frame (ID/credit **card** = 85.6 mm, an
   **ArUco** marker, or a **manual** mm-per-pixel) converts glyph pixels → millimetres.
4. **Fusion** — merges the three into the scan-input contract (translating the LLM's
   coarse `front`/`back` into the engine's panel vocabulary, filtering symbols, etc.).

### Mock mode — runs today, no key, no heavy install

Every layer has a dependency-free mock, so the whole photo → verdict path works offline.
Pass `--mock` (CLI), `mock=true` (API form field), or set `LABEL_JAANO_MOCK=1`. Mock reads
a `<image>.mock.json` sidecar next to the image if present (see `samples/*.mock.json`),
else returns a canned compliant read.

```bash
cd backend

# photo(s) -> scan-input JSON (front + back), offline mock
python3 extract.py samples/label_front.png samples/label_back.png --mock

# photo(s) -> full verdict, with a reference so Rule 8 heights are measured in mm
python3 extract.py samples/label_front.png samples/label_back.png --mock --evaluate \
    --reference '{"type":"manual","mm_per_px":0.0531,"pdp_bbox":[40,40,820,1220]}'
#   -> ✅ COMPLIANT 100/100, net_quantity measured at 2.5 mm

python3 tests/test_pipeline.py     # 15 mock tests, no deps
```

`extract.py` mirrors `run_scan.py`: add `--evaluate` to score, `--json` for machine
output, `--context '{...}'` for officer overrides, `--category` to force the class;
exit code `0`/`1`/`2` = compliant/needs-review/non-compliant.

### Real mode

```bash
pip install -r requirements.txt        # includes the extraction extras (heavy)
export GEMINI_API_KEY=...              # or GOOGLE_API_KEY
# On Apple Silicon, if paddlepaddle wheels are a problem, install it separately first.
python3 extract.py front.jpg back.jpg --evaluate \
    --reference '{"type":"card","width_mm":85.6,"bbox":[x,y,w,h]}'
```

The heavy deps (`google-generativeai`, `paddleocr`, `paddlepaddle`, `opencv-python`,
`pillow`, `numpy`) are **optional** — imported lazily, only when a real (non-mock) call
runs. The engine and mock pipeline never touch them.

### Over HTTP (multipart upload)

```bash
# photo(s) -> scan-input JSON
curl -s -X POST http://127.0.0.1:8000/extract \
     -F "images=@samples/label_front.png" -F "images=@samples/label_back.png" \
     -F 'reference={"type":"manual","mm_per_px":0.0531,"pdp_bbox":[40,40,820,1220]}' \
     -F "mock=true"

# photo(s) -> full ComplianceReport in one shot
curl -s -X POST http://127.0.0.1:8000/scan/image \
     -F "images=@samples/label_front.png" -F "images=@samples/label_back.png" \
     -F "mock=true"
```

Both accept repeated `images=@...` parts plus optional `reference`, `context`,
`category`, and `mock` form fields. Omit `mock` in production and the server
auto-detects: real models if the deps + key are present, mock otherwise.

## Accounts, history and reports

An inspection nobody can retrieve later is worth very little, so the API also files
what it judged. This half is **stdlib too** — `sqlite3` for storage, `hmac`/`hashlib`
for passwords and tokens, `html` for the report — which is why `requirements.txt` did
not grow a single line to gain accounts, history, role-scoped queries and a
print-ready report.

### Turn it on

Nothing to do: the database is created on first use at `data/labeljaano.db`. To do it
deliberately, or to make the first admin (the one role sign-up can never grant itself):

```bash
cd backend
python3 manage.py init                                  # create/upgrade the schema
python3 manage.py createuser --email you@gov.in --role admin --password-stdin
python3 manage.py seed                                  # optional demo corpus
```

Then sign in over HTTP and use the token:

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/auth/login \
        -H "Content-Type: application/json" \
        -d '{"email":"you@gov.in","password":"..."}' | python3 -c 'import json,sys;print(json.load(sys.stdin)["token"])')

# scan and file in one call — same endpoint, now with a token
curl -s -X POST http://127.0.0.1:8000/scan/image -H "Authorization: Bearer $TOKEN" \
     -F "images=@samples/label_front.png" -F "mock=true" \
     -F "product_name=Parle-G 100g" -F "location=Pune, MH"

curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/scans
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/stats
```

### Roles

| Role       | Sees                       | Granted by                                    |
|------------|----------------------------|-----------------------------------------------|
| `consumer` | their own inspections      | open sign-up (the default)                    |
| `officer`  | **every** filed inspection | sign-up **with** the shared enrolment code    |
| `admin`    | every filed inspection     | `manage.py createuser` / `manage.py role` only|

`/scans` and `/stats` report the scope they actually searched (`"own"` / `"all"`), so
the client states whose records it is showing rather than inferring it from the role.
Another account's inspection answers **404**, not 403 — a consumer cannot use error
codes to discover that a record exists.

### Sharing a report

A phone cannot print, and a browser address bar cannot send an `Authorization`
header. `POST /scans/{id}/share` therefore mints a **separate, weaker credential**: a
ticket bound to that one inspection, valid 15 minutes by default (`?minutes=`, max
120), refused anywhere a session token is expected, and dead the moment the account is
disabled. Forwarding the link hands over one report, never the account.

```bash
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
     "http://127.0.0.1:8000/scans/$SCAN_ID/share?minutes=30"
# -> {"scan_id":"…","path":"/scans/<id>/report.html?ticket=<ticket>",
#     "ticket":"…","expires_at":"2026-08-29T12:34:56Z","expires_in_seconds":1800}
```

Open that URL in a browser and print to PDF. `manage.py report <scan_id> -o out.html`
renders the same document offline.

### Environment

| Variable | Default | Effect |
|----------|---------|--------|
| `LABEL_JAANO_DB` | `backend/data/labeljaano.db` | where the SQLite file lives |
| `LABEL_JAANO_SECRET` | per-process random | token signing key. **Set it in production** — leaving it unset means every restart signs everyone out. `/health` and `/auth/config` advertise which mode you are in, so the omission is visible rather than silent. `manage.py secret` prints a good value. |
| `LABEL_JAANO_OFFICER_CODE` | unset | shared enrolment code that lets sign-up choose `officer`. Unset ⇒ nobody can self-register as an officer; use `manage.py role`. |
| `LABEL_JAANO_NO_DB` | unset | `1` runs the API as a pure stateless judge: no database file, accounts and history return **503**, and scanning is completely unaffected. This is the read-only demo mode. |
| `LABEL_JAANO_MOCK` | unset | `1` forces the offline extraction mock (see above) |
| `LABEL_JAANO_RULEPACKS` | `../rulepacks` | where to load the JSON packs from |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | unset | enables the real vision-LLM read |

Session tokens last 12 hours — one inspection shift — and `POST /auth/refresh` slides
an active one forward.

### `manage.py` — the admin CLI

Stdlib only, talks to the same database the server does (it deliberately has no
independent default path, so the two can never drift apart).

```
init          create or upgrade the database
createuser    create an account (the only way to make an admin)
users         list accounts
role          change an account's role
disable       disable an account (revokes its tokens at once)
passwd        reset a password
scans         list stored inspections (--user --verdict --category --search)
report        export one inspection as a print-ready HTML report
stats         corpus aggregates
delete-scan   delete one stored inspection
secret        generate a value for LABEL_JAANO_SECRET
seed          populate a demo corpus for a walkthrough
```

Passwords come from a prompt, or `--password-stdin` for scripts. `--password` exists
and says so in its own help text: it is visible in shell history and `ps`.

```bash
python3 tests/test_store.py && python3 tests/test_auth.py && python3 tests/test_reports.py
```

## The scan-input contract

This is the JSON the OCR + vision-LLM pipeline must produce (see `samples/` for full
examples). `fields` is keyed by **declaration id** — the same ids used in the rule
packs — which is what lets rules and extracted values line up automatically.

```jsonc
{
  "category": "packaged_food",          // vision-LLM classification -> selects packs
  "raw_text": "full label text ...",     // used by format checks with target=raw_text
  "fields": {
    "mrp": {
      "value": "Maximum Retail Price Rs 10 inclusive of all taxes",
      "panel": "principal_display_panel", // for placement checks
      "height_mm": 3.1,                    // for font_height checks (null if unmeasured)
      "confidence": 0.94
    }
    // ... one entry per declaration the pipeline could read
  },
  "symbols_detected": ["veg_nonveg_mark", "fssai_logo"],  // from the CV layer
  "context": {                            // flags that drive conditional declarations
    "is_imported": false,
    "is_single_ingredient": false,
    "has_additives": false,
    "has_allergens": false,
    "dimension_relevant": false,
    "pdp_area_cm2": 300                   // principal-display-panel area for Rule 8
  }
}
```

## Using it from code

```python
from rule_engine import ScanInput, evaluate_scan

scan = ScanInput.from_dict(pipeline_output)
report = evaluate_scan(scan)             # picks + merges packs by scan.category

print(report.verdict, report.score)      # Verdict.NON_COMPLIANT 61.5
for v in report.violations:
    print(v.severity.value, v.legal_reference, "-", v.message)
```

## Extending it

* **New/updated government rule** → edit a JSON pack in `../rulepacks/`. No code change.
* **New check kind** (validator or symbol) → add it to the registry in `checks.py`
  (`VALIDATORS` or `KNOWN_SYMBOLS`), then reference it from a pack.
* **New regulation** (cosmetics, drugs, BIS…) → drop a new pack file; it's auto-selected
  by `applies_when.category_in`. See `../rulepacks/README.md` and `../REGULATORY_PACKS.md`.

## Design note: the engine stays pure

The engine only ever consumes the *already-normalized* scan-input contract, and it is
pure stdlib. The pipeline (OCR, Gemini, calibration) sits **upstream** and fills that
contract; swapping mock for real models, or PaddleOCR for another OCR, requires zero
engine changes. That separation is why the engine is trivially testable and why a
gazette rule update is just a JSON edit + `/reload`.

Still upstream / future work: a Postgres backend for multi-node deployments (the
`store/` module is the only place that would change), and native PDF/DOCX rendering
of the inspection report — the HTML one already prints correctly, which is what an
inspector actually needs. See `../ARCHITECTURE.md`.
