#!/usr/bin/env python3
"""
Tests for the Label Jaano HTTP API (FastAPI).

Runs two ways:
    pytest                     # from the backend/ directory
    python3 tests/test_api.py  # self-contained runner (still needs fastapi + httpx)

Uses Starlette's TestClient (requires ``httpx``) so no server needs to be running.
These tests confirm the API faithfully surfaces the engine: the known-good sample
must come back COMPLIANT over HTTP, the broken sample NON-COMPLIANT, and the
rulepack / health / reload plumbing must work.

The second half covers accounts, history and export. Those are mostly *negative*
tests, because the interesting failures are all leaks:

* ``test_other_users_scan_is_404_not_403`` — a wrong answer here turns the endpoint
  into an oracle for which scan ids exist.
* ``test_login_failures_are_indistinguishable`` — the same, for which emails are
  registered.
* ``test_officer_cannot_delete_another_inspectors_scan`` — reading the corpus and
  destroying part of it are separate permissions on purpose.
* ``test_no_response_ever_carries_a_password_hash`` — sweeps every response shape.
* ``test_anonymous_scanning_still_works`` — the one that protects the product: a
  consumer must get a verdict with no account at all.
* ``test_a_share_ticket_is_not_a_login`` — the report link and the session token are
  signed by the same key, so this proves they are not interchangeable.

Every test that touches persistence calls :func:`_reset_db` first, which points the
store at a fresh in-memory database. Nothing here writes a file. The client and the
account fixtures come from :mod:`tests.apiclient`, shared with the admin, export and
rate-limit modules so that "what an officer is" is defined in exactly one place.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Importing this configures the store for ``:memory:`` *before* the app is imported,
# which is why it comes first — see its module docstring.
from apiclient import (  # noqa: E402
    BACKEND,
    OFFICER_CODE,
    PASSWORD,
    SAMPLES,
    client,
)
import apiclient  # noqa: E402
import store  # noqa: E402

from app import deps  # noqa: E402
from auth.registration import OFFICER_CODE_ENV  # noqa: E402

_sample = apiclient.sample
_reset_db = apiclient.reset_db
_register = apiclient.register
_headers = apiclient.headers
_consumer = apiclient.consumer
_officer = apiclient.officer
_admin = apiclient.admin
_file_scan = apiclient.scan


# --------------------------------------------------------------------------- #
# Meta
# --------------------------------------------------------------------------- #
def test_health_ok():
    r = client.get("/health")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["packs_loaded"] >= 2
    assert "legal_metrology_2011" in body["pack_ids"]


def test_openapi_docs_served():
    # the auto-generated schema should list our /scan route
    r = client.get("/openapi.json")
    assert r.status_code == 200, r.text
    assert "/scan" in r.json()["paths"]


# --------------------------------------------------------------------------- #
# Rulepacks
# --------------------------------------------------------------------------- #
def test_list_rulepacks():
    r = client.get("/rulepacks")
    assert r.status_code == 200, r.text
    packs = r.json()
    ids = {p["pack_id"] for p in packs}
    assert {"legal_metrology_2011", "fssai_food_2020"} <= ids
    base = next(p for p in packs if p["pack_id"] == "legal_metrology_2011")
    assert base["scope"] == "base"
    assert base["declarations"] >= 9


def test_get_one_rulepack_full_json():
    r = client.get("/rulepacks/legal_metrology_2011")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pack_id"] == "legal_metrology_2011"
    # the full rule text, not just a summary
    assert isinstance(body["declarations"], list) and body["declarations"]
    assert "font_height_table" in body


def test_get_unknown_rulepack_404():
    r = client.get("/rulepacks/does_not_exist")
    assert r.status_code == 404, r.text


# --------------------------------------------------------------------------- #
# Scan (the important ones)
# --------------------------------------------------------------------------- #
def test_scan_good_label_compliant():
    r = client.post("/scan", json=_sample("good_label.json"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verdict"] == "compliant", body["violations"]
    assert body["score"] == 100.0
    assert {"legal_metrology_2011", "fssai_food_2020"} <= set(body["packs_applied"])
    assert body["summary"]["failed"] == 0


def test_scan_bad_label_non_compliant():
    r = client.post("/scan", json=_sample("bad_label.json"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verdict"] == "non_compliant", body
    assert body["summary"]["violations_by_severity"]["critical"] >= 4
    # each violation carries its legal citation
    assert all(v["legal_reference"] for v in body["violations"])


def test_scan_minimal_payload_defaults():
    # a nearly-empty body must still validate and run (category defaults to unknown
    # -> only the base pack applies) rather than 422.
    r = client.post("/scan", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["packs_applied"] == ["legal_metrology_2011"]


def test_scan_ignores_extra_comment_key():
    # samples carry a leading "_comment"; pydantic must ignore unknown top-level keys
    payload = _sample("good_label.json")
    payload["_comment"] = "this should be ignored, not 422"
    r = client.post("/scan", json=payload)
    assert r.status_code == 200, r.text


# --------------------------------------------------------------------------- #
# Tier-2 reference standards (must survive response_model=ReportOut)
# --------------------------------------------------------------------------- #
def test_scan_surfaces_reference_standards():
    # A nutraceutical scan carries Tier-2 reference standards; response_model=ReportOut
    # must pass them through to the client (this field was silently stripped before).
    r = client.post("/scan", json={
        "category": "nutraceutical",
        "raw_text": "VITA HEALTH SUPPLEMENT. NOT FOR MEDICINAL USE. Out of reach of children.",
        "fields": {"net_quantity": {"value": "60 capsules"},
                   "mrp": {"value": "MRP Rs 499 inclusive of all taxes"}},
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert "reference_standards" in body, "response_model stripped the Tier-2 block"
    refs = body["reference_standards"]
    assert isinstance(refs, list) and len(refs) >= 2, refs
    for ref in refs:
        assert ref["id"] and ref["label"] and ref["legal_reference"], ref
        assert "condition" not in ref  # internal gating token stays server-side


def test_scan_reference_standards_are_not_scored_checks():
    # refs must be disjoint from the scored results/violations in the response
    r = client.post("/scan", json={"category": "packaged_food",
                                    "fields": {"net_quantity": {"value": "100 g"}}})
    assert r.status_code == 200, r.text
    body = r.json()
    ref_ids = {ref["id"] for ref in body["reference_standards"]}
    result_ids = {res["declaration_id"] for res in body["results"]}
    assert ref_ids, "expected contaminants refs for a food product"
    assert not (ref_ids & result_ids), "a reference standard leaked into scored results"


def test_scan_accepts_new_context_flags():
    # ContextIn must model the new trigger flags: a wine scan with is_wine=true must
    # not 422 AND must actually surface the wine-only reference standard.
    r = client.post("/scan", json={
        "category": "wine",
        "raw_text": ("Wine 13% v/v ABV. CONSUMPTION OF ALCOHOL IS INJURIOUS TO HEALTH. "
                     "DON'T DRINK AND DRIVE."),
        "fields": {"net_quantity": {"value": "750 ml"}},
        "context": {"is_wine": True},
    })
    assert r.status_code == 200, r.text
    ref_ids = {ref["id"] for ref in r.json()["reference_standards"]}
    assert "wine_animal_fining_logo" in ref_ids, ref_ids


# --------------------------------------------------------------------------- #
# Reload
# --------------------------------------------------------------------------- #
def test_reload_returns_pack_count():
    """Reload is admin-only: it pushes a gazette amendment live for every user at once."""
    _reset_db()
    admin = _admin()
    r = client.post("/reload", headers=_headers(admin["access_token"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "reloaded"
    assert body["packs_loaded"] >= 2


def test_reload_is_refused_to_everyone_below_admin():
    """An officer enforces the rules; changing them is a different job.

    Anonymous gets 401 (no identity), officer gets 403 (identified but not allowed) —
    the distinction matters, because 403 tells a real officer their session is fine and
    it is the permission that is wrong.
    """
    _reset_db()
    assert client.post("/reload").status_code == 401
    officer = _officer()
    r = client.post("/reload", headers=_headers(officer["access_token"]))
    assert r.status_code == 403, r.text
    assert "not permitted" in r.json()["detail"].lower()


# --------------------------------------------------------------------------- #
# Accounts
# --------------------------------------------------------------------------- #
def test_auth_config_advertises_what_this_server_offers():
    """The sign-up screen renders from this, so it must not claim officer sign-up is
    possible on a server where no enrolment code is set."""
    _reset_db()
    body = client.get("/auth/config").json()
    assert body["accounts_available"] is True
    assert body["officer_signup_enabled"] is True
    assert body["min_password_length"] >= 8
    assert isinstance(body["ephemeral_secret"], bool)

    os.environ.pop(OFFICER_CODE_ENV, None)
    assert client.get("/auth/config").json()["officer_signup_enabled"] is False


def test_register_returns_a_usable_token():
    _reset_db()
    body = _register("new@test.in")
    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0
    assert body["user"]["email"] == "new@test.in"
    assert body["user"]["role"] == "consumer"
    # The token must actually work, not merely be well-formed.
    me = client.get("/auth/me", headers=_headers(body["access_token"]))
    assert me.status_code == 200, me.text
    assert me.json()["email"] == "new@test.in"


def test_register_normalises_the_email():
    _reset_db()
    body = _register("  MiXeD@Test.IN  ")
    assert body["user"]["email"] == "mixed@test.in"


def test_register_rejects_a_weak_password():
    _reset_db()
    r = client.post("/auth/register", json={"email": "weak@test.in", "password": "abc"})
    assert r.status_code == 400, r.text
    assert "password" in r.json()["detail"].lower()


def test_register_rejects_a_duplicate_email():
    _reset_db()
    _register("dup@test.in")
    r = client.post("/auth/register",
                    json={"email": "DUP@test.in", "password": PASSWORD})
    assert r.status_code == 409, r.text


def test_login_round_trip():
    _reset_db()
    _register("login@test.in")
    r = client.post("/auth/login", json={"email": "login@test.in", "password": PASSWORD})
    assert r.status_code == 200, r.text
    assert r.json()["user"]["email"] == "login@test.in"


def test_login_failures_are_indistinguishable():
    """Different messages for "no such account" and "wrong password" turn the login
    form into an account-enumeration oracle."""
    _reset_db()
    _register("real@test.in")
    wrong_password = client.post("/auth/login",
                                 json={"email": "real@test.in", "password": "wrong-one"})
    no_account = client.post("/auth/login",
                             json={"email": "ghost@test.in", "password": PASSWORD})
    assert wrong_password.status_code == no_account.status_code == 401
    assert wrong_password.json()["detail"] == no_account.json()["detail"], (
        "the two failures are distinguishable by message")


def test_officer_signup_needs_the_right_code():
    _reset_db()
    no_code = client.post("/auth/register", json={
        "email": "a@test.gov.in", "password": PASSWORD, "role": "officer"})
    assert no_code.status_code == 403, no_code.text

    wrong_code = client.post("/auth/register", json={
        "email": "b@test.gov.in", "password": PASSWORD, "role": "officer",
        "officer_code": "not-the-code"})
    assert wrong_code.status_code == 403, wrong_code.text

    assert _officer("c@test.gov.in")["user"]["role"] == "officer"


def test_officer_signup_is_unavailable_without_a_configured_code():
    _reset_db()
    os.environ.pop(OFFICER_CODE_ENV, None)
    r = client.post("/auth/register", json={
        "email": "d@test.gov.in", "password": PASSWORD, "role": "officer",
        "officer_code": "anything"})
    assert r.status_code == 403, r.text


def test_signup_cannot_request_admin():
    """Privilege must not be obtainable from a public form, even with the officer code
    in hand."""
    _reset_db()
    r = client.post("/auth/register", json={
        "email": "root@test.in", "password": PASSWORD, "role": "admin",
        "officer_code": OFFICER_CODE})
    if r.status_code == 201:
        assert r.json()["user"]["role"] != "admin", "sign-up granted admin"
    else:
        assert r.status_code == 403, r.text


def test_me_requires_a_token():
    _reset_db()
    assert client.get("/auth/me").status_code == 401


def test_a_broken_token_is_401_not_silently_anonymous():
    """Downgrading a bad token to anonymous would let a lapsed session keep scanning
    while its history silently stopped being recorded."""
    _reset_db()
    for bad in ("not-a-token", "a.b.c", ""):
        r = client.get("/auth/me", headers={"Authorization": f"Bearer {bad}"})
        assert r.status_code == 401, f"{bad!r} -> {r.status_code}"


def test_refresh_issues_a_new_token_for_a_valid_one():
    _reset_db()
    token = _consumer()["access_token"]
    r = client.post("/auth/refresh", headers=_headers(token))
    assert r.status_code == 200, r.text
    assert client.get("/auth/me", headers=_headers(r.json()["access_token"])).status_code == 200


def test_refresh_rejects_an_absent_token():
    _reset_db()
    assert client.post("/auth/refresh").status_code == 401


# --------------------------------------------------------------------------- #
# Scanning + persistence
# --------------------------------------------------------------------------- #
def test_anonymous_scanning_still_works():
    """Half the product. A consumer must get a verdict with no account at all — and be
    told plainly that nothing was filed."""
    _reset_db()
    r = client.post("/scan", json=_sample("good_label.json"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verdict"] == "compliant"
    assert body["saved"] is False
    assert body["scan_id"] is None


def test_a_signed_in_scan_is_filed():
    _reset_db()
    body = _file_scan(_consumer()["access_token"])
    assert body["saved"] is True
    assert body["scan_id"], "a filed scan must come back with its reference"


def test_save_false_returns_a_verdict_without_filing_it():
    _reset_db()
    body = _file_scan(_consumer()["access_token"], save=False)
    assert body["verdict"] == "compliant"
    assert body["saved"] is False and body["scan_id"] is None


def test_field_metadata_is_recorded_with_the_scan():
    _reset_db()
    token = _consumer()["access_token"]
    filed = _file_scan(token, product_name="Tata Salt 1kg", note="Routine",
                       location="Shop 4, Pune")
    detail = client.get(f"/scans/{filed['scan_id']}", headers=_headers(token)).json()
    assert detail["product_name"] == "Tata Salt 1kg"
    assert detail["note"] == "Routine"
    assert detail["location"] == "Shop 4, Pune"


# --------------------------------------------------------------------------- #
# History — scoping is the whole design
# --------------------------------------------------------------------------- #
def test_history_requires_an_account():
    _reset_db()
    for path in ("/scans", "/stats"):
        assert client.get(path).status_code == 401, path


def test_a_consumer_sees_only_their_own_history():
    _reset_db()
    a = _consumer("a@test.in")["access_token"]
    b = _consumer("b@test.in")["access_token"]
    _file_scan(a)
    _file_scan(b)
    _file_scan(b)

    mine = client.get("/scans", headers=_headers(a)).json()
    assert mine["total"] == 1
    assert mine["scope"] == "own"


def test_an_officer_sees_the_whole_corpus():
    """The reason the role exists: enforcement intelligence needs every inspection,
    not one inspector's."""
    _reset_db()
    consumer = _consumer("shopper@test.in")["access_token"]
    officer = _officer()["access_token"]
    _file_scan(consumer)
    _file_scan(consumer, "bad_label.json")

    corpus = client.get("/scans", headers=_headers(officer)).json()
    assert corpus["total"] == 2, "the officer queue is missing consumer scans"
    assert corpus["scope"] == "all"


def test_the_list_view_omits_the_report_body():
    """A list that carried every full report would grow with the corpus."""
    _reset_db()
    token = _consumer()["access_token"]
    _file_scan(token)
    item = client.get("/scans", headers=_headers(token)).json()["items"][0]
    assert "report" not in item or item.get("report") is None
    assert item["verdict"] and item["score"] is not None


def test_history_filters_and_paginates():
    _reset_db()
    token = _consumer()["access_token"]
    _file_scan(token, product_name="Tata Salt")
    _file_scan(token, "bad_label.json", product_name="Toffee Jar")

    only_compliant = client.get("/scans", headers=_headers(token),
                                params={"verdict": "compliant"}).json()
    assert only_compliant["total"] == 1

    searched = client.get("/scans", headers=_headers(token),
                          params={"search": "Toffee"}).json()
    assert searched["total"] == 1

    page = client.get("/scans", headers=_headers(token),
                      params={"limit": 1, "offset": 0}).json()
    assert len(page["items"]) == 1
    assert page["total"] == 2, "total is the match count, not the page size"


def test_bad_pagination_is_rejected():
    _reset_db()
    token = _consumer()["access_token"]
    assert client.get("/scans", headers=_headers(token),
                      params={"limit": 0}).status_code == 422
    assert client.get("/scans", headers=_headers(token),
                      params={"limit": 5000}).status_code == 422
    assert client.get("/scans", headers=_headers(token),
                      params={"offset": -1}).status_code == 422


def test_scan_detail_carries_the_verbatim_report():
    _reset_db()
    token = _consumer()["access_token"]
    filed = _file_scan(token, "bad_label.json")
    detail = client.get(f"/scans/{filed['scan_id']}", headers=_headers(token))
    assert detail.status_code == 200, detail.text
    stored = detail.json()["report"]
    assert stored["verdict"] == filed["verdict"]
    assert stored["score"] == filed["score"]
    assert len(stored["violations"]) == len(filed["violations"])


def test_other_users_scan_is_404_not_403():
    """403 would confirm the id exists. The scoped lookup finds nothing instead, so the
    response cannot be used to discover which ids are real."""
    _reset_db()
    a = _consumer("a@test.in")["access_token"]
    b = _consumer("b@test.in")["access_token"]
    filed = _file_scan(a)

    seen = client.get(f"/scans/{filed['scan_id']}", headers=_headers(b))
    invented = client.get("/scans/00000000000000000000000000000000",
                          headers=_headers(b))
    assert seen.status_code == 404, seen.text
    assert invented.status_code == 404
    assert seen.json()["detail"] == invented.json()["detail"], (
        "the two 404s are distinguishable, which leaks that the id exists")


def test_a_consumer_can_delete_their_own_scan():
    _reset_db()
    token = _consumer()["access_token"]
    filed = _file_scan(token)
    assert client.delete(f"/scans/{filed['scan_id']}",
                         headers=_headers(token)).status_code == 204
    assert client.get(f"/scans/{filed['scan_id']}",
                      headers=_headers(token)).status_code == 404


def test_officer_cannot_delete_another_inspectors_scan():
    """Reading the corpus and destroying part of it are separate permissions.
    Destroying someone else's evidence must not be a side effect of reviewing it."""
    _reset_db()
    consumer = _consumer("shopper@test.in")["access_token"]
    officer = _officer()["access_token"]
    filed = _file_scan(consumer)

    # Visible to the officer...
    assert client.get(f"/scans/{filed['scan_id']}",
                      headers=_headers(officer)).status_code == 200
    # ...but not deletable by them.
    assert client.delete(f"/scans/{filed['scan_id']}",
                         headers=_headers(officer)).status_code == 404
    assert client.get(f"/scans/{filed['scan_id']}",
                      headers=_headers(consumer)).status_code == 200, (
        "the scan was destroyed despite the refusal")


def test_stats_scope_follows_the_role():
    _reset_db()
    consumer = _consumer("shopper@test.in")["access_token"]
    officer = _officer()["access_token"]
    _file_scan(consumer)
    _file_scan(consumer, "bad_label.json")

    own = client.get("/stats", headers=_headers(consumer)).json()
    assert own["scope"] == "own" and own["total_scans"] == 2

    everything = client.get("/stats", headers=_headers(officer)).json()
    assert everything["scope"] == "all" and everything["total_scans"] == 2
    assert everything["violations_total"] > 0
    assert everything["top_violations"], "the dashboard needs ranked violations"


# --------------------------------------------------------------------------- #
# Report export
# --------------------------------------------------------------------------- #
def test_report_html_is_served_as_a_printable_document():
    _reset_db()
    token = _consumer()["access_token"]
    filed = _file_scan(token, "bad_label.json")
    r = client.get(f"/scans/{filed['scan_id']}/report.html", headers=_headers(token))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/html")
    assert r.text.startswith("<!DOCTYPE html>")
    assert "@page" in r.text and "A4" in r.text


def test_report_html_appendix_is_switchable():
    _reset_db()
    token = _consumer()["access_token"]
    filed = _file_scan(token, "bad_label.json")
    url = f"/scans/{filed['scan_id']}/report.html"
    assert "full assessment log" in client.get(url, headers=_headers(token)).text
    assert "full assessment log" not in client.get(
        url, headers=_headers(token), params={"appendix": False}).text


def test_report_html_download_sets_a_filename():
    _reset_db()
    token = _consumer()["access_token"]
    filed = _file_scan(token)
    r = client.get(f"/scans/{filed['scan_id']}/report.html",
                   headers=_headers(token), params={"download": True})
    assert r.status_code == 200
    disposition = r.headers.get("content-disposition", "")
    assert disposition.startswith("attachment")
    assert "label-jaano-report-" in disposition


def test_report_attributes_the_officer_only_to_their_own_findings():
    """An officer reviewing someone else's inspection must not have their own name
    printed in the signature block of that finding."""
    _reset_db()
    consumer = _consumer("shopper@test.in")["access_token"]
    officer = _officer("inspector@test.gov.in")["access_token"]
    theirs = _file_scan(consumer)
    mine = _file_scan(officer)

    reviewing = client.get(f"/scans/{theirs['scan_id']}/report.html",
                           headers=_headers(officer)).text
    own = client.get(f"/scans/{mine['scan_id']}/report.html",
                     headers=_headers(officer)).text
    assert "inspector@test.gov.in" not in reviewing, (
        "the reviewing officer was named as the assessor of someone else's inspection")
    assert "inspector@test.gov.in" in own


def test_report_html_requires_auth():
    _reset_db()
    token = _consumer()["access_token"]
    filed = _file_scan(token)
    assert client.get(f"/scans/{filed['scan_id']}/report.html").status_code == 401


def test_report_for_an_unknown_scan_is_404():
    _reset_db()
    token = _consumer()["access_token"]
    assert client.get("/scans/nope/report.html",
                      headers=_headers(token)).status_code == 404


# --------------------------------------------------------------------------- #
# Share links
#
# A phone cannot print. Getting a report onto paper means getting it into a browser,
# and an address bar cannot send an Authorization header — so the report endpoint also
# accepts a purpose-scoped ticket. Everything below exists to prove that ticket is not
# a second, weaker way to log in.
# --------------------------------------------------------------------------- #
def _share(token: str, scan_id: str, **params) -> dict:
    r = client.post(f"/scans/{scan_id}/share", headers=_headers(token), params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_a_share_link_opens_the_report_with_no_token():
    _reset_db()
    token = _consumer()["access_token"]
    filed = _file_scan(token, "bad_label.json")
    link = _share(token, filed["scan_id"])

    assert link["scan_id"] == filed["scan_id"]
    assert link["expires_in_seconds"] == 900
    assert link["expires_at"].endswith("Z")
    # The path is relative and self-sufficient: the client joins it to its own base URL.
    assert link["path"].startswith(f"/scans/{filed['scan_id']}/report.html?ticket=")

    r = client.get(link["path"])          # note: no headers at all
    assert r.status_code == 200, r.text
    assert r.text.startswith("<!DOCTYPE html>")
    assert r.headers["content-type"].startswith("text/html")


def test_a_share_ticket_is_not_a_login():
    """The failure this guards against is a report link forwarded over WhatsApp turning
    into a full API credential."""
    _reset_db()
    token = _consumer()["access_token"]
    filed = _file_scan(token)
    ticket = _share(token, filed["scan_id"])["ticket"]

    for path in ("/auth/me", "/scans", "/stats"):
        r = client.get(path, headers=_headers(ticket))
        assert r.status_code == 401, f"{path} accepted a share ticket as a bearer token"
        assert "report share link" in r.json()["detail"].lower()


def test_a_share_ticket_is_bound_to_one_inspection():
    _reset_db()
    token = _consumer()["access_token"]
    mine = _file_scan(token, "good_label.json")
    other = _file_scan(token, "bad_label.json")
    ticket = _share(token, mine["scan_id"])["ticket"]

    # Same owner, same ticket, different scan — still refused. The ticket authorises a
    # document, not an account.
    r = client.get(f"/scans/{other['scan_id']}/report.html", params={"ticket": ticket})
    assert r.status_code == 401, r.text


def test_a_tampered_or_absent_ticket_is_refused():
    _reset_db()
    token = _consumer()["access_token"]
    filed = _file_scan(token)
    url = f"/scans/{filed['scan_id']}/report.html"
    ticket = _share(token, filed["scan_id"])["ticket"]

    assert client.get(url).status_code == 401
    assert client.get(url, params={"ticket": ""}).status_code == 401
    assert client.get(url, params={"ticket": "not-a-ticket"}).status_code == 401
    assert client.get(url, params={"ticket": ticket[:-4] + "AAAA"}).status_code == 401
    # Sanity: the untampered ticket does work, so the assertions above are meaningful.
    assert client.get(url, params={"ticket": ticket}).status_code == 200


def test_sharing_widens_nothing():
    """You can only share what you could already read, so the link grants a consumer no
    reach they did not have, and an officer no more than their review scope."""
    _reset_db()
    shopper = _consumer("shopper@test.in")["access_token"]
    stranger = _consumer("stranger@test.in")["access_token"]
    officer = _officer()["access_token"]
    filed = _file_scan(shopper)

    # 404, not 403: consistent with GET /scans/{id}, so this cannot be used to probe
    # which scan ids exist.
    assert client.post(f"/scans/{filed['scan_id']}/share",
                       headers=_headers(stranger)).status_code == 404
    # An officer may review the corpus, so they may share from it.
    assert client.post(f"/scans/{filed['scan_id']}/share",
                       headers=_headers(officer)).status_code == 200
    # And minting one requires an account in the first place.
    assert client.post(f"/scans/{filed['scan_id']}/share").status_code == 401


def test_a_shared_report_keeps_its_attribution_rules():
    """The signature block follows the *inspection*, not the link. An officer sharing
    someone else's finding must not sign it."""
    _reset_db()
    shopper = _consumer("shopper@test.in")["access_token"]
    officer = _officer("inspector@test.gov.in")["access_token"]
    theirs = _file_scan(shopper)
    mine = _file_scan(officer)

    reviewed = client.get(_share(officer, theirs["scan_id"])["path"]).text
    own = client.get(_share(officer, mine["scan_id"])["path"]).text
    assert "inspector@test.gov.in" not in reviewed
    assert "inspector@test.gov.in" in own


def test_a_share_link_lifetime_is_bounded():
    _reset_db()
    token = _consumer()["access_token"]
    filed = _file_scan(token)
    url = f"/scans/{filed['scan_id']}/share"

    assert _share(token, filed["scan_id"], minutes=120)["expires_in_seconds"] == 7200
    for absurd in (0, -5, 121, 60 * 24 * 365):
        r = client.post(url, headers=_headers(token), params={"minutes": absurd})
        assert r.status_code == 422, f"minutes={absurd} was accepted"


def test_a_share_link_dies_with_its_account():
    """A link must not outlive the account that issued it — otherwise disabling a
    compromised officer would leave their outstanding links working."""
    _reset_db()
    account = _consumer("leaver@test.in")
    token = account["access_token"]
    filed = _file_scan(token)
    path = _share(token, filed["scan_id"])["path"]
    assert client.get(path).status_code == 200

    store.set_disabled(account["user"]["id"], True)
    assert client.get(path).status_code == 403


def test_a_shared_report_can_still_be_downloaded():
    _reset_db()
    token = _consumer()["access_token"]
    filed = _file_scan(token)
    link = _share(token, filed["scan_id"])
    r = client.get(link["path"] + "&download=true&appendix=false")
    assert r.status_code == 200, r.text
    assert r.headers.get("content-disposition", "").startswith("attachment")
    assert "full assessment log" not in r.text


def test_share_is_unavailable_without_persistence():
    _reset_db()
    token = _consumer()["access_token"]
    filed = _file_scan(token)
    os.environ[deps.PERSISTENCE_DISABLED_ENV] = "1"
    try:
        deps.init_persistence()
        r = client.post(f"/scans/{filed['scan_id']}/share", headers=_headers(token))
        # 503 rather than 401: the router-level persistence guard runs first, so the
        # answer names the real problem instead of blaming the caller's token.
        assert r.status_code == 503, r.text
    finally:
        os.environ.pop(deps.PERSISTENCE_DISABLED_ENV, None)
        _reset_db()


# --------------------------------------------------------------------------- #
# Cross-cutting
# --------------------------------------------------------------------------- #
def test_no_response_ever_carries_a_password_hash():
    """One sweep over every response shape that includes a user, because a verifier is
    always exactly one careless response model away from shipping."""
    _reset_db()
    token = _consumer("leak@test.in")["access_token"]
    filed = _file_scan(token)
    responses = [
        client.post("/auth/login", json={"email": "leak@test.in", "password": PASSWORD}),
        client.get("/auth/me", headers=_headers(token)),
        client.post("/auth/refresh", headers=_headers(token)),
        client.get("/scans", headers=_headers(token)),
        client.get(f"/scans/{filed['scan_id']}", headers=_headers(token)),
        client.get("/stats", headers=_headers(token)),
    ]
    for r in responses:
        assert r.status_code == 200, f"{r.request.url}: {r.text}"
        blob = r.text.lower()
        for needle in ("password_hash", "pbkdf2", PASSWORD):
            assert needle not in blob, f"{needle!r} leaked from {r.request.url}"


def test_health_reports_history_availability_honestly():
    _reset_db()
    body = client.get("/health").json()
    assert body["history_available"] is True
    assert body["status"] == "ok"


def test_openapi_lists_the_new_routes():
    paths = client.get("/openapi.json").json()["paths"]
    for route in ("/auth/register", "/auth/login", "/auth/me", "/scans",
                  "/scans/{scan_id}", "/scans/{scan_id}/share",
                  "/scans/{scan_id}/report.html", "/stats"):
        assert route in paths, f"{route} is missing from the OpenAPI schema"


def test_history_answers_503_when_persistence_is_disabled():
    """A read-only demo box runs with LABEL_JAANO_NO_DB=1. Scanning must be unaffected
    and history must say why it is unavailable rather than 500."""
    _reset_db()
    token = _consumer()["access_token"]
    os.environ[deps.PERSISTENCE_DISABLED_ENV] = "1"
    try:
        deps.init_persistence()
        assert deps.persistence_ready() is False
        assert client.get("/scans", headers=_headers(token)).status_code == 503
        assert client.get("/auth/config").json()["accounts_available"] is False
        # The engine is the product; it needs no database.
        r = client.post("/scan", json=_sample("good_label.json"))
        assert r.status_code == 200, r.text
        assert r.json()["verdict"] == "compliant"
        assert r.json()["saved"] is False
    finally:
        os.environ.pop(deps.PERSISTENCE_DISABLED_ENV, None)
        _reset_db()


def test_the_cors_allow_list_is_configurable_and_defaults_to_open() -> None:
    """``LABEL_JAANO_CORS_ORIGINS`` narrows CORS; unset means ``*``.

    The default has to stay ``*``, because the demo runs the Flutter app from a phone
    on the LAN and Chrome from localhost, and neither has a stable origin worth
    naming. That default is only defensible because ``allow_credentials`` is False —
    so this test pins that too. If someone ever flips credentials on while origins are
    still ``*``, Starlette will happily reflect any origin *and* send cookies, and
    every signed-in browser becomes vulnerable to any page on the internet.
    """
    from app import main as app_main

    was = os.environ.get(app_main.CORS_ORIGINS_ENV)
    try:
        os.environ.pop(app_main.CORS_ORIGINS_ENV, None)
        assert app_main._cors_origins() == ["*"], "the demo default must stay open"

        # Whitespace, a trailing slash and a stray empty entry are all operator typos
        # in a comma-separated env var, not reasons to fail to boot.
        os.environ[app_main.CORS_ORIGINS_ENV] = (
            " https://console.example.gov.in/ , https://app.example.gov.in ,"
        )
        assert app_main._cors_origins() == [
            "https://console.example.gov.in",
            "https://app.example.gov.in",
        ]

        # A value that is nothing but separators is a misconfiguration; falling back to
        # the documented default beats booting with an empty allow-list that would
        # reject every browser with no clue why.
        os.environ[app_main.CORS_ORIGINS_ENV] = " , , "
        assert app_main._cors_origins() == ["*"]
    finally:
        if was is None:
            os.environ.pop(app_main.CORS_ORIGINS_ENV, None)
        else:
            os.environ[app_main.CORS_ORIGINS_ENV] = was

    # The pairing that makes the open default safe.
    cors = next(m for m in app_main.app.user_middleware
                if "CORS" in m.cls.__name__)
    assert cors.kwargs["allow_credentials"] is False, (
        "allow_origins=['*'] is only safe without credentials"
    )


# --------------------------------------------------------------------------- #
# Self-contained runner (no pytest required)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    raise SystemExit(apiclient.run_all(globals(), title="API tests"))
