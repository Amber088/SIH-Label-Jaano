# Label Jaano — Backend / Rule Engine

The compliance **rule engine** — the heart of the backend. It takes the normalized
output of the OCR + vision-LLM pipeline and judges it against the versioned JSON
rule packs in [`../rulepacks/`](../rulepacks/), producing a verdict, a 0–100 score,
and a list of violations that each cite their exact legal reference.

The **engine** is **pure Python standard library** — no dependencies to install
before you can run it. A thin **FastAPI layer** (`app/`) wraps it for HTTP; that layer
is the only part that needs `pip install`. The database and OCR/vision services plug in
later (see `../ARCHITECTURE.md`); keeping the engine dependency-free keeps it trivially
testable and portable.

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
│   └── main.py       # endpoints: /scan /extract /scan/image /health /rulepacks /reload
├── samples/
│   ├── good_label.json   # a compliant packaged-food label (scan-input JSON)
│   ├── bad_label.json    # a deliberately non-compliant one
│   ├── label_front.png   # rendered demo photo (front panel) + .mock.json sidecar
│   ├── label_back.png    # rendered demo photo (back panel) + .mock.json sidecar
│   └── make_fixtures.py  # regenerates the demo photos + sidecars
├── tests/
│   ├── test_engine.py    # engine end-to-end + unit tests (no deps)
│   ├── test_pipeline.py  # pipeline end-to-end + unit tests (mock, no deps)
│   └── test_api.py       # API tests via TestClient
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

| Method | Path              | Purpose                                                    |
|--------|-------------------|------------------------------------------------------------|
| GET    | `/health`         | liveness + how many packs are loaded                       |
| GET    | `/rulepacks`      | summary of every loaded rule pack                          |
| GET    | `/rulepacks/{id}` | full raw JSON of one pack (audit exactly what's enforced)  |
| POST   | `/scan`           | judge one normalized label (JSON) → `ComplianceReport`     |
| POST   | `/extract`        | label **photo(s)** → normalized scan-input JSON            |
| POST   | `/scan/image`     | label **photo(s)** → `ComplianceReport` (extract + judge)  |
| POST   | `/reload`         | re-read `rulepacks/` from disk — hot-apply a gazette update |

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

Still upstream / future work: persistence (Postgres), auth, and rendered PDF/DOCX
reports — see `../ARCHITECTURE.md`.
