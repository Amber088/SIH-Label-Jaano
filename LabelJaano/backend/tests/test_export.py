#!/usr/bin/env python3
"""
Tests for the bulk CSV export (``GET /scans.csv``) and category discovery
(``GET /categories``).

Runs two ways:
    pytest                        # from the backend/ directory
    python3 tests/test_export.py  # no pytest needed — self-contained runner

The export is what makes the corpus usable as evidence rather than only as a screen, so
the guarantees are about the *file*, not the endpoint:

* ``test_the_file_agrees_with_what_the_officer_just_read`` — the filters must select
  exactly the rows ``GET /scans`` selects. A file that quietly disagrees with the
  queue it was exported from is worse than no export.
* ``test_the_export_is_scoped_not_privileged`` — a consumer exporting gets their own
  inspections. Bulk output is exactly where a forgotten WHERE clause becomes a leak of
  the whole table.
* ``test_truncation_is_stated_in_the_file`` and its boundary twin — a truncated
  register that looks complete is the failure mode worth engineering against, and the
  warning must never be a false alarm at an exact boundary.
* ``test_the_file_opens_correctly_in_excel_on_windows`` — a BOM and CRLF endings. Ugly,
  and the reason is that the officers this is for are on Windows, where a BOM-less
  UTF-8 CSV is read as the system codepage and every Devanagari product name is
  mangled.

``GET /categories`` is here rather than in its own module because it is the same
"stop hardcoding what the packs already know" concern seen from the client side: the
list must be the union of what the packs on disk claim, every entry must actually be
scoreable, and the order must be stable so the dropdown does not reshuffle itself
between two phones.
"""
from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import apiclient  # noqa: E402
import store  # noqa: E402
from apiclient import client, headers, reset_db  # noqa: E402
from app.main import FALLBACK_CATEGORY, get_packs  # noqa: E402
from app.routers.history import _CSV_COLUMNS, MAX_EXPORT_ROWS  # noqa: E402
from rule_engine import build_ruleset  # noqa: E402


BOM = "﻿"


def _csv(token: str, **params) -> tuple[list[list[str]], object]:
    """``(rows_including_header, response)`` for one export, BOM stripped."""
    r = client.get("/scans.csv", headers=headers(token), params=params)
    assert r.status_code == 200, r.text
    text = r.text
    assert text.startswith(BOM), "no BOM — see test_the_file_opens_correctly_in_excel"
    return list(csv.reader(io.StringIO(text[len(BOM):]))), r


def _data_rows(token: str, **params) -> list[list[str]]:
    """Just the inspections: no header, and no trailing ``# TRUNCATED`` comment."""
    rows, _ = _csv(token, **params)
    return [r for r in rows[1:] if r and not r[0].startswith("#")]


def _column(rows: list[list[str]], name: str) -> list[str]:
    return [r[_CSV_COLUMNS.index(name)] for r in rows]


def _ids(rows: list[list[str]]) -> list[str]:
    return _column(rows, "scan_id")


def _file_scan(token: str, *, name: str, category: str = "packaged_food",
               bad: bool = False, **extra) -> str:
    """File one inspection under a given product name and category. Returns its id."""
    payload = apiclient.sample("bad_label.json" if bad else "good_label.json")
    payload["category"] = category
    r = client.post("/scan", json=payload, headers=headers(token),
                    params={"product_name": name, **extra})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["saved"] is True, body
    return body["scan_id"]


# --------------------------------------------------------------------------- #
# The file itself
# --------------------------------------------------------------------------- #
def test_the_header_is_exactly_the_documented_columns():
    """The column list is a published contract, not a projection of ``ScanRow``.

    Somebody's spreadsheet has ``=D2`` in it. If adding a field to the row silently
    inserted a column, that formula would start reading a different number without
    anything failing, so the header is asserted against the literal tuple.
    """
    reset_db()
    tok = apiclient.officer()["access_token"]
    rows, _ = _csv(tok)
    assert rows[0] == list(_CSV_COLUMNS)
    assert len(rows[0]) == 19, "column count changed — is that intended?"


def test_the_file_opens_correctly_in_excel_on_windows():
    """A BOM, CRLF endings, and a charset in the content type.

    All three are concessions to one real deployment fact: the officers this is for
    open the file in Excel on Windows, where a BOM-less UTF-8 CSV is decoded as the
    system codepage and every Devanagari product name arrives mangled. Asserted
    because it is exactly the kind of detail a later refactor "cleans up".
    """
    reset_db()
    tok = apiclient.officer()["access_token"]
    _file_scan(tok, name="कुरकुरे मसाला मंच")

    r = client.get("/scans.csv", headers=headers(tok))
    assert r.status_code == 200
    assert r.headers["content-type"] == "text/csv; charset=utf-8"
    assert r.text.startswith(BOM)
    body = r.text[len(BOM):]
    assert "\r\n" in body
    # Every terminator is a CRLF: no line ends in a bare LF.
    assert body.replace("\r\n", "") .count("\n") == 0
    assert "कुरकुरे मसाला मंच" in body, "non-ASCII product name did not survive"
    assert r.headers["content-disposition"].startswith("attachment; filename=")


def test_one_inspection_becomes_one_row_carrying_the_scan_it_came_from():
    """The numbers in the file are the numbers the engine returned.

    A register whose counts are re-derived at export time can drift from the report it
    claims to summarise, so each cell is checked against the ``/scan`` response body
    rather than against another query.
    """
    reset_db()
    who = apiclient.officer()
    tok = who["access_token"]
    r = client.post("/scan", json=apiclient.sample("bad_label.json"),
                    headers=headers(tok),
                    params={"product_name": "Namkeen 200g", "note": "shelf 3",
                            "location": "Kanpur"})
    assert r.status_code == 200, r.text
    body = r.json()

    rows = _data_rows(tok)
    assert len(rows) == 1
    row = dict(zip(_CSV_COLUMNS, rows[0]))
    assert row["scan_id"] == body["scan_id"]
    assert row["verdict"] == body["verdict"] == "non_compliant"
    assert row["score"] == f"{body['score']:.1f}"
    assert row["category"] == body["category"]
    assert row["product_name"] == "Namkeen 200g"
    assert row["note"] == "shelf 3"
    assert row["location"] == "Kanpur"
    summary = body["summary"]
    for col in ("checks_total", "passed", "failed", "skipped"):
        assert row[col] == str(summary[col]), col
    for sev in ("critical", "major", "minor"):
        assert row[sev] == str(summary["violations_by_severity"][sev]), sev
    assert row["source"] == "json"
    assert row["mock"] == "no"
    assert row["packs_applied"].split() == body["packs_applied"]
    assert row["user_id"] == who["user"]["id"]


# --------------------------------------------------------------------------- #
# Who gets which rows
# --------------------------------------------------------------------------- #
def test_the_export_is_scoped_not_privileged():
    """A consumer exporting gets their own inspections and nobody else's.

    Bulk output is exactly where a forgotten WHERE clause stops being a bug and becomes
    a disclosure of the whole table, so this is asserted from both ends: the consumer's
    file contains all of their own rows and none of the other two accounts'.
    """
    reset_db()
    alice = apiclient.consumer("alice@test.in")["access_token"]
    bob = apiclient.consumer("bob@test.in")["access_token"]
    cop = apiclient.officer()["access_token"]

    mine = {_file_scan(alice, name="Alice A"), _file_scan(alice, name="Alice B")}
    theirs = {_file_scan(bob, name="Bob A"), _file_scan(cop, name="Officer A")}

    got = set(_ids(_data_rows(alice)))
    assert got == mine
    assert not (got & theirs)

    # The officer's copy is the whole corpus — that is the point of the role.
    assert set(_ids(_data_rows(cop))) == mine | theirs


def test_the_filename_states_which_scope_it_was_taken_at():
    """``-own-`` or ``-all-`` in the attachment name.

    Two exports of the same district land in the same downloads folder, and "is this
    everything or just mine?" is not a question the file should leave open.
    """
    reset_db()
    who = apiclient.consumer()
    cop = apiclient.officer()
    _file_scan(who["access_token"], name="Mine")

    for token, tag, other in ((who["access_token"], "-own-", "-all-"),
                              (cop["access_token"], "-all-", "-own-")):
        r = client.get("/scans.csv", headers=headers(token))
        disposition = r.headers["content-disposition"]
        assert tag in disposition, disposition
        assert other not in disposition, disposition
        assert disposition.endswith('.csv"'), disposition


def test_the_export_needs_a_login():
    reset_db()
    assert client.get("/scans.csv").status_code == 401


# --------------------------------------------------------------------------- #
# The file must not disagree with the screen
# --------------------------------------------------------------------------- #
def _mixed_corpus(token: str) -> None:
    """Six inspections spanning two verdicts, two categories and distinct names."""
    _file_scan(token, name="Bhujia 200g", category="packaged_food")
    _file_scan(token, name="Bhujia 400g", category="packaged_food", bad=True)
    _file_scan(token, name="Cola 750ml", category="beverage")
    _file_scan(token, name="Cola 2L", category="beverage", bad=True)
    _file_scan(token, name="Bisleri 1L", category="packaged_water")
    _file_scan(token, name="Whisky 750ml", category="liquor", bad=True,
               note="seized at Kanpur depot")


def test_the_file_agrees_with_what_the_officer_just_read():
    """Every filter selects the same rows here as on ``GET /scans``.

    This is the guarantee the export exists for. An officer narrows the queue on screen,
    clicks export, and attaches the result to a notice; a file that quietly selected a
    different set of rows than the screen it was taken from is worse than no export at
    all, because the disagreement is invisible until someone disputes the finding.

    Compared as sets, because the two endpoints deliberately order oppositely — see
    :func:`test_the_register_reads_forward_in_time`.
    """
    reset_db()
    tok = apiclient.officer()["access_token"]
    _mixed_corpus(tok)

    filters = (
        {},
        {"verdict": "compliant"},
        {"verdict": "non_compliant"},
        {"category": "beverage"},
        {"category": "liquor", "verdict": "non_compliant"},
        {"search": "Bhujia"},
        {"search": "Cola 2L"},
        {"search": "Kanpur"},                      # matches on the note, not the name
        {"category": "beverage", "search": "750"},
        {"verdict": "no_label_detected"},          # legitimately empty
        {"category": "nonexistent_category"},      # ditto
    )
    for params in filters:
        screen = client.get("/scans", headers=headers(tok),
                            params={**params, "limit": 200})
        assert screen.status_code == 200, screen.text
        expected = {item["id"] for item in screen.json()["items"]}
        assert set(_ids(_data_rows(tok, **params))) == expected, params


def test_the_register_reads_forward_in_time():
    """Oldest first — the opposite of the on-screen queue, on purpose.

    A screen answers "what happened last", so it is newest first. A register is read
    forward from the start of the period it covers, and an evidence annexure that begins
    at the end of the story is a nuisance to read in a hearing.
    """
    reset_db()
    tok = apiclient.officer()["access_token"]
    _mixed_corpus(tok)

    exported = _ids(_data_rows(tok))
    screen = client.get("/scans", headers=headers(tok), params={"limit": 200}).json()
    assert exported == [item["id"] for item in screen["items"]][::-1]

    stamps = _column(_data_rows(tok), "created_at")
    assert stamps == sorted(stamps), "timestamps are not ascending"


def test_a_note_with_commas_and_quotes_survives_the_round_trip():
    """Quoting is delegated to :mod:`csv`, and this proves it was not bypassed.

    An officer's note is free text typed on a phone. The failure mode is not a crash —
    it is one row that parses as seven columns, shifting every later cell in that row
    and quietly corrupting the counts a reader trusts.
    """
    reset_db()
    tok = apiclient.officer()["access_token"]
    nasty = 'Seized 3 packs, "MRP overwritten", ₹45 → ₹60; ref 12,500'
    _file_scan(tok, name='Bhujia "Special", 200g', note=nasty, location="Kanpur, UP")

    rows = _data_rows(tok)
    assert len(rows) == 1, "the note broke the row into more than one line"
    row = dict(zip(_CSV_COLUMNS, rows[0]))
    assert len(rows[0]) == len(_CSV_COLUMNS)
    assert row["note"] == nasty
    assert row["product_name"] == 'Bhujia "Special", 200g'
    assert row["location"] == "Kanpur, UP"


def test_a_newline_in_a_note_does_not_become_a_new_row():
    """Newlines in a note are flattened to spaces rather than quoted.

    Quoted newlines are legal CSV and every naive line-oriented tool mishandles them.
    The verbatim text is still in the stored report, so nothing is lost that matters;
    what is bought is that ``wc -l`` on the export is the number of inspections.
    """
    reset_db()
    tok = apiclient.officer()["access_token"]
    _file_scan(tok, name="Multi-line", note="line one\nline two\r\nline\tthree")

    rows, response = _csv(tok)
    assert len(rows) == 2, rows            # header plus exactly one inspection
    note = dict(zip(_CSV_COLUMNS, rows[1]))["note"]
    assert note == "line one line two line three"
    assert "\n" not in note and "\t" not in note
    # Three body lines would mean the flattening happened only in the parsed view.
    assert response.text[len(BOM):].rstrip("\r\n").count("\r\n") == 1


# --------------------------------------------------------------------------- #
# Truncation — the failure mode worth engineering against
# --------------------------------------------------------------------------- #
def test_truncation_is_stated_in_the_file():
    """A short file must announce that it is short, in the file.

    A response header would be dropped by every spreadsheet, and a truncated register
    that looks complete is how an enforcement action gets built on a subset nobody knew
    was a subset. ``max_rows`` is used to reach the ceiling at test scale; the production
    ceiling is the same code path with a bigger number.
    """
    reset_db()
    tok = apiclient.officer()["access_token"]
    for n in range(5):
        _file_scan(tok, name=f"Item {n}")

    rows, response = _csv(tok, max_rows=3)
    body = [r for r in rows[1:] if r]
    data, warning = body[:-1], body[-1]
    assert len(data) == 3
    assert warning[0].startswith("# TRUNCATED at 3 rows")
    assert "narrow the filters" in warning[0]
    # The warning is a comment row, not a mangled inspection: a reader parsing the file
    # by column must not mistake it for data.
    assert all(not r[0].startswith("#") for r in data)
    assert "TRUNCATED" in response.text

    # Narrowing the filters is the advice the warning gives, so it had better work.
    narrowed, _ = _csv(tok, max_rows=3, search="Item 4")
    assert not any(r[0].startswith("#") for r in narrowed[1:] if r)


def test_the_truncation_warning_is_not_a_false_alarm_at_the_boundary():
    """A corpus of exactly ``max_rows`` is complete, and must not claim otherwise.

    This is what the deliberate ``max_rows + 1`` fetch in the endpoint buys, and it is
    an easy thing to lose in a refactor: the naive implementation warns whenever it
    wrote as many rows as it was allowed to, which is a lie exactly at the boundary.
    """
    reset_db()
    tok = apiclient.officer()["access_token"]
    for n in range(3):
        _file_scan(tok, name=f"Item {n}")

    at_boundary, _ = _csv(tok, max_rows=3)
    assert len(at_boundary) == 4, at_boundary          # header + 3, no warning
    assert not any(r and r[0].startswith("#") for r in at_boundary)

    one_under, _ = _csv(tok, max_rows=2)
    assert any(r and r[0].startswith("#") for r in one_under), "should have warned"


def test_the_row_ceiling_cannot_be_argued_upward():
    """``max_rows`` is validated, so the cap is not a suggestion."""
    reset_db()
    tok = apiclient.officer()["access_token"]
    for value in (MAX_EXPORT_ROWS + 1, 0, -1, 10 ** 9):
        r = client.get("/scans.csv", headers=headers(tok), params={"max_rows": value})
        assert r.status_code == 422, (value, r.status_code)
    ok = client.get("/scans.csv", headers=headers(tok),
                    params={"max_rows": MAX_EXPORT_ROWS})
    assert ok.status_code == 200


# --------------------------------------------------------------------------- #
# Streaming
# --------------------------------------------------------------------------- #
def test_no_row_is_lost_or_repeated_across_batch_boundaries():
    """A corpus larger than one batch exports exactly once per row.

    The export walks the table with keyset pagination on ``(created_at, id)``, and this
    is the test for the tie-breaking half of that key. ``created_at`` has one-second
    resolution, so these rows nearly all share a timestamp — the same collision a busy
    district produces — and a cursor comparing only ``created_at`` would either skip the
    rest of a colliding group or loop on it forever.

    Written through ``store`` rather than over HTTP because the point is 1 200 rows, and
    1 200 rule-engine runs would make this the slowest test in the suite for no extra
    coverage.
    """
    reset_db()
    who = apiclient.officer()
    tok = who["access_token"]
    payload = client.post("/scan", json=apiclient.sample("good_label.json"),
                          params={"save": "false"}).json()

    count = store.EXPORT_BATCH * 2 + 3          # spans three batches, ends mid-batch
    filed = [store.save_scan(payload, user_id=who["user"]["id"],
                             product_name=f"Item {n:04d}").id
             for n in range(count)]
    assert len({*filed}) == count

    ids = _ids(_data_rows(tok, max_rows=MAX_EXPORT_ROWS))
    assert len(ids) == count, f"exported {len(ids)} of {count}"
    assert len(set(ids)) == count, "a row was exported twice"
    assert set(ids) == set(filed)

    stamps = _column(_data_rows(tok, max_rows=MAX_EXPORT_ROWS), "created_at")
    assert stamps == sorted(stamps)


def test_an_empty_corpus_still_exports_a_usable_header():
    """Nothing to report is a valid answer, and a zero-byte file is not it.

    An officer whose filter matched nothing should get a file that opens and shows the
    columns, so the answer reads as "no inspections match" rather than as a failure.
    """
    reset_db()
    tok = apiclient.officer()["access_token"]
    rows, response = _csv(tok)
    assert rows == [list(_CSV_COLUMNS)]
    assert response.headers["content-type"] == "text/csv; charset=utf-8"


def test_an_absent_note_is_an_empty_cell_not_the_word_none():
    """Optional columns are blank when unset.

    ``None`` rendered into a cell is the kind of thing that ends up in a filed annexure
    and has to be explained, and it also breaks the "is this column empty" test every
    spreadsheet user reaches for first.
    """
    reset_db()
    tok = apiclient.officer()["access_token"]
    r = client.post("/scan", json=apiclient.sample("good_label.json"),
                    headers=headers(tok))
    assert r.status_code == 200, r.text

    row = dict(zip(_CSV_COLUMNS, _data_rows(tok)[0]))
    for col in ("product_name", "note", "location"):
        assert row[col] == "", (col, row[col])
    assert "None" not in ",".join(row.values())
    assert row["mock"] in ("yes", "no")


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #
def test_only_the_whole_corpus_export_is_recorded():
    """Taking a copy of everyone's inspections is an event. Exporting your own is not.

    Read through ``store`` rather than ``GET /admin/audit`` so that what is measured is
    what this endpoint wrote, not what another endpoint chose to show.

    The filters are recorded with it, because "an officer exported the corpus" and "an
    officer exported the eleven non-compliant water scans in Kanpur" are different
    events, and only the second one can be checked against a reason later.
    """
    reset_db()
    consumer_tok = apiclient.consumer()["access_token"]
    cop = apiclient.officer()
    _file_scan(consumer_tok, name="Mine")

    assert client.get("/scans.csv", headers=headers(consumer_tok)).status_code == 200
    entries, _ = store.audit.list_entries(action=store.audit.CORPUS_EXPORT)
    assert entries == [], "a consumer exporting their own history is not an event"

    r = client.get("/scans.csv", headers=headers(cop["access_token"]),
                   params={"verdict": "non_compliant", "category": "packaged_water",
                           "search": "Kanpur", "max_rows": 25})
    assert r.status_code == 200
    entries, total = store.audit.list_entries(action=store.audit.CORPUS_EXPORT)
    assert total == 1, entries
    entry = entries[0]
    assert entry.actor_id == cop["user"]["id"]
    assert entry.actor_email == "officer@test.gov.in"
    assert entry.actor_role == "officer"
    assert entry.detail["verdict"] == "non_compliant"
    assert entry.detail["category"] == "packaged_water"
    assert entry.detail["search"] == "Kanpur"
    assert entry.detail["max_rows"] == 25


def test_a_failed_export_is_not_recorded_as_one():
    """A rejected request must not leave a line saying the corpus was taken.

    An audit log that records attempts as if they succeeded is worse than a shorter one:
    every entry then has to be corroborated before it means anything.
    """
    reset_db()
    apiclient.officer()
    assert client.get("/scans.csv").status_code == 401
    assert client.get("/scans.csv", headers=headers("not-a-token")).status_code == 401
    _, total = store.audit.list_entries(action=store.audit.CORPUS_EXPORT)
    assert total == 0


# --------------------------------------------------------------------------- #
# GET /categories — the other half of "stop hardcoding the list"
# --------------------------------------------------------------------------- #
def _categories() -> list[dict]:
    r = client.get("/categories")
    assert r.status_code == 200, r.text
    return r.json()


def test_categories_are_readable_without_a_login():
    """No token needed: this is the picker a consumer sees before signing up.

    Gating it would mean the app either ships a hardcoded list again or shows an empty
    dropdown to a first-time user, which is the problem the endpoint was added to solve.
    """
    reset_db()
    r = client.get("/categories")
    assert r.status_code == 200
    assert r.json(), "no categories discovered at all"


def test_categories_come_from_the_packs_on_disk():
    """Every category is claimed by a pack — or is the catch-all.

    The point of the endpoint is that dropping a pack into ``rulepacks/`` makes its
    categories selectable without a new mobile build, so the list is asserted to be
    exactly the union of the packs' ``applies_when.category_in`` plus ``other``. A
    hand-maintained list would drift from that union the first time a pack changed.
    """
    reset_db()
    packs = get_packs()
    union = {FALLBACK_CATEGORY}
    for pack in packs:
        union.update((pack.applies_when or {}).get("category_in") or [])

    ids = [c["id"] for c in _categories()]
    assert set(ids) == union
    assert len(ids) == len(set(ids)), "a category was listed twice"
    # The regression this endpoint exists for: the app used to ship four.
    assert len(ids) > 4, f"only {len(ids)} categories — packs not loading?"


def test_the_catch_all_is_pinned_last_however_rich_it_is():
    """``other`` sorts last, and is still a real choice rather than a null.

    It is the "not sure" option, and a picker that offers it early invites an inspector
    to take it instead of naming the commodity — which loses the category packs and with
    them most of the checks. With the packs currently on disk it is also the smallest, so
    the explicit pin is belt-and-braces; it is asserted anyway because the day a base
    pack grows is the day the ordering would silently change.
    """
    reset_db()
    cats = _categories()
    assert cats[-1]["id"] == FALLBACK_CATEGORY
    assert cats[-1]["label"] == "Other / not sure"
    # A commodity we cannot classify is not a commodity exempt from Legal Metrology.
    assert cats[-1]["packs"], "the catch-all scores nothing"
    assert cats[-1]["declarations"] > 0
    assert cats[-1]["authorities"], "no regulator cited for the catch-all"


def test_the_richest_category_leads_and_ties_are_stable():
    """Most checks first, alphabetical within a tie.

    The first entry is what a picker should default to, and it should be the category
    that scores the most. The tie-break matters for a different reason: several
    categories share a pack set exactly (``beverage``/``food``/``packaged_food``), and
    without an explicit tie-break their order would come from set iteration and change
    between processes — so the same dropdown would reorder itself between two phones.
    """
    reset_db()
    cats = _categories()
    body = cats[:-1]                                   # the catch-all is pinned, skip it

    counts = [c["declarations"] for c in body]
    assert counts == sorted(counts, reverse=True), counts
    assert counts[0] == max(c["declarations"] for c in cats)

    for group_count in set(counts):
        tied = [c["id"] for c in body if c["declarations"] == group_count]
        assert tied == sorted(tied), tied

    # Stable across calls, not merely sorted once.
    assert [c["id"] for c in _categories()] == [c["id"] for c in cats]


def test_the_base_regulator_leads_the_authority_list():
    """Legal Metrology first, then the sector regulator.

    ``authorities`` is what a report cites, and the base pack is the authority that
    applies to every packaged commodity — so it leads. De-duplicated in ``packs_applied``
    order rather than through a set, because a set would reorder the citation line
    between runs of the same server.
    """
    reset_db()
    for cat in _categories():
        authorities = cat["authorities"]
        assert authorities, cat["id"]
        assert len(authorities) == len(set(authorities)), cat["id"]
        assert "Legal Metrology" in authorities[0], (cat["id"], authorities)


def test_the_label_is_human_readable_rather_than_an_id():
    """Labels are derived, not carried in the pack JSON.

    A pack is regulation-as-data shared by every client; a display string is a UI concern
    that would then need translating. So the id is title-cased, with a small table for
    the ones where that reads badly — and no label may leak an underscore.
    """
    reset_db()
    by_id = {c["id"]: c["label"] for c in _categories()}
    assert by_id["packaged_food"] == "Packaged food"
    assert by_id["other"] == "Other / not sure"
    assert by_id["food_special_medical"] == "Food for special medical purpose"
    for cid, label in by_id.items():
        assert "_" not in label, (cid, label)
        assert label.strip() == label and label, cid


def test_the_declaration_count_is_the_merged_ruleset():
    """``declarations`` is the merged, id-overridden count — not a sum of pack counts.

    Summing the packs' own counts would double-count anything a category pack overrides,
    which is precisely what merging exists to resolve. Checked against ``build_ruleset``,
    which is the same function the scan path uses, so the number a picker shows cannot
    drift from the ruleset a scan is actually judged against.
    """
    reset_db()
    packs = get_packs()
    for cat in _categories():
        ruleset = build_ruleset(cat["id"], packs)
        assert cat["packs"] == ruleset.packs_applied, cat["id"]
        assert cat["declarations"] == len(ruleset.declarations), cat["id"]
        ids = [d.id for d in ruleset.declarations]
        assert len(ids) == len(set(ids)), f"{cat['id']}: merge left a duplicate id"


def test_every_advertised_category_is_actually_scoreable():
    """Offering a category in the picker and being able to score it are the same thing.

    The list is computed from the packs, so an unscoreable entry would mean a pack
    declares a category nothing can evaluate — an inspector would pick it and get an
    error in the field. ``save=false`` because this is about the engine, not history.
    """
    reset_db()
    for cat in _categories():
        payload = apiclient.sample("good_label.json")
        payload["category"] = cat["id"]
        r = client.post("/scan", json=payload, params={"save": "false"})
        assert r.status_code == 200, (cat["id"], r.text)
        body = r.json()
        assert body["category"] == cat["id"]
        assert body["packs_applied"] == cat["packs"], cat["id"]
        assert body["summary"]["checks_total"] > 0, cat["id"]
        assert body["verdict"] in ("compliant", "needs_review", "non_compliant",
                                   "no_label_detected"), body["verdict"]


if __name__ == "__main__":
    raise SystemExit(apiclient.run_all(globals(), title="export + categories tests"))


