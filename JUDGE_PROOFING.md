# LabelJaano — judge-proofing list

Ordered by credibility gained per hour spent. Everything here fits inside a week.
Scope is defensive: close the gaps a judge would find, don't add features.

Current honest score: **8.0 / 10** as an SIH backend.

Items 1 and 3 are done as of 2026-09-01; item 2 is half done. Everything else stands.

---

## Do these first — cheap, and each one is a question you stop losing

### 1. Fix the doc drift  ✅ DONE (2026-09-01)
`README.md` documented the wrong env var and the wrong model. A judge who copy-pastes
your own setup instructions and gets mock mode will not trust the rest of the deck.

- Env var: `backend/README.md:239` said `LABEL_JAANO_GEMINI_API_KEY` → now `GEMINI_API_KEY`.
- Model: `backend/README.md:29,250` and `ARCHITECTURE.md:113,175` said `Gemini 2.0 Flash`
  → now `gemini-3.6-flash`, matching `pipeline/gemini.py:39`.
- Test count: `backend/README.md:276` said "15 mock tests" → now 19.

A repo-wide grep for `Gemini 2.0 Flash` and `LABEL_JAANO_GEMINI_API_KEY` returns nothing
outside this file.

### 2. Make mock mode explicit-only and loud  (~30 min — PARTLY DONE)
Right now a missing key silently falls back to mock. On a teammate's laptop, in
Docker without `-e`, or in CI, that means a fabricated clean result with no warning.

- ✅ **Startup now says which mode it is in.** `app/main.py:_log_extraction_mode()` runs
  in the lifespan and logs `extraction mode=LIVE` or a `WARNING` with
  `extraction mode=MOCK — ... returns CANNED label values`. It reuses the existing
  `resolve_mock_mode()` so there is no second source of truth.
- ⬜ Still to do: require `LABEL_JAANO_MOCK=1` to *enter* mock mode, 503 rather than a
  fake verdict when there is no key and no mock flag, and stamp `"mode": "mock"` into
  every mock response body.

The startup line converts the failure from "silently wrong" to "visibly unconfigured",
but only for whoever is watching the log. The response-body stamp is what protects a
judge reading the app.

### 3. Deal with the inert `.env`  ✅ DONE (2026-09-01)
`backend/.env` was never loaded — no `dotenv` import anywhere, `python-dotenv` in no
requirements file. It read like a config that worked and wasn't one. Now:

- `backend/envfile.py` — a ~40-line stdlib loader, no new dependency (the rest of the
  backend is stdlib by design, and `python-dotenv` only arrived as a transitive
  dependency of `uvicorn[standard]`, which is not something to rely on).
- Wired in ahead of every environment read in `app/main.py`, `extract.py` and
  `manage.py`. The ordering matters: `auth/tokens.py:79` captures `LABEL_JAANO_SECRET`
  at *import* time, so loading inside a startup hook would have been too late.
- Three behaviours, each earned from a real mistake: the shell wins over the file;
  blank/placeholder values are ignored so an unfilled line cannot clobber a good
  export (which is exactly what `uvicorn --env-file .env` does); `<angle brackets>`
  are stripped.
- `backend/.env.example` is committed (`.gitignore:19` un-ignores it); `.env` itself
  stays ignored at `.gitignore:17`.
- `tests/test_envfile.py` — 10 tests, dependency-free standalone runner, all green.

---

## Then these — they harden the actual claims on the deck

### 4. Tighten the spoofable format regexes  (~2–3 hrs)
Several `format` checks match loosely enough that unrelated text on the label can
satisfy them. Add word boundaries and require nearby keyword context so a check
passes because the declaration is present, not because a substring happened to appear.

### 5. Add `tests/test_adversarial.py`  (blocked by #4)
Negative-match tests: labels that should FAIL and currently pass. Use the existing
standalone dependency-free runner pattern (`python3 tests/test_engine.py`) so it
runs in this sandbox without PyPI.

### 6. Stop `negate` checks rewarding blank labels  (~2 hrs)
`backend/rule_engine/checks.py:284-286` — a prohibition check on an empty field
currently counts as a pass, so a label with nothing on it collects points for every
prohibition it "didn't" violate. Return SKIP when there is no text to judge.

### 7. Rewrite the oversold wording in the docs  (~1 hr)
`README.md` and `ARCHITECTURE.md` describe "multi-source fusion" and "confidence
calibration". In fact `fusion.py` is a field normalizer, and `calibration.py` is
geometric px→mm, not probability calibration — its three `confidence` constants are
hardcoded and nothing downstream reads them. The deck already says this honestly;
the docs shouldn't contradict it. A judge who greps and finds the gap discounts
everything else you claimed.

### 8. Rehearse the two demo beats  (~1 hr)
These are your strongest moments and they need to be muscle memory:

- **Pack override:** scan the same label as `packaged_food`, then as `wine`. Show
  `packs_applied` change and the MRP "inclusive of all taxes" check drop out under
  the Rule 6(1)(e) proviso.
- **Two-tier:** show that the 12 reference standards produce 0 scored checks, and
  that `fssai_contaminants_2011` carries 5 limits and 0 declarations by design.

---

## Stretch — only if the above is done

- **Make `confidence` gate one real decision.** Right now it is decorative. Have it
  suppress or downgrade at least one finding so the field means something.
- **Rename or repair the circular end-to-end fixture test** — it currently asserts
  against a fixture derived from the same code path it is testing.
- **Full verification pass:** run all 308 tests plus one manual real-key scan of a
  genuine package and one of a non-label (should return `NO_LABEL` / `0.0`).

---

## Before you submit the deck

The template mandates PDF — `LabelJaano_SIH2026_Idea_Presentation.pdf` is the file
to upload. PPT and Word are not accepted.

Five blanks on slide 1 need values from the SIH portal, plus one oval:

- Problem Statement ID
- Problem Statement Title
- Theme
- Team ID
- Team Name (as registered on the portal)
- The "Your Team Name" oval on slides 2–6

Re-export the PDF after filling them.
