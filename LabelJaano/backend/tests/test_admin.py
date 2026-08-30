#!/usr/bin/env python3
"""
Tests for the administration endpoints (``/admin/*``) and the audit trail.

Runs two ways:
    pytest                       # from the backend/ directory
    python3 tests/test_admin.py  # no pytest needed — self-contained runner

Three guarantees here are the reason the module exists, and each one is a failure that
would only be discovered at the worst possible moment:

* ``test_the_last_admin_cannot_lock_the_server_out`` — demoting or disabling the only
  administrator who can still sign in must be refused. If it were allowed, recovery
  needs shell access to the database file, which on a deployed server is exactly what
  you do not have when you need it.
* ``test_a_mistyped_role_is_refused_not_silently_downgraded`` — ``Role.parse`` degrades
  anything unrecognised to ``consumer``, which is correct for a value read back from
  the database and wrong for one an admin typed. ``offcier`` must be an error, not a
  silent demotion of the account they meant to promote.
* ``test_a_refused_delete_leaves_no_trace`` and its neighbours — the log must record
  every privileged action and *only* privileged actions. An entry for a 404 would make
  the trail a record of attempts rather than of what happened, and a missing entry for
  a real deletion makes it useless as evidence.

The audit assertions read the trail through ``store`` rather than ``GET /admin/audit``,
so that what is being measured is what the endpoint under test recorded, with no second
request in between. The endpoint's own filtering is covered separately.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import apiclient  # noqa: E402
import store  # noqa: E402
from apiclient import PASSWORD, client, headers, reset_db  # noqa: E402
from auth.roles import Role  # noqa: E402

ADMIN_ROUTES = ("/admin/users", "/admin/audit")


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def _admin_h(email: str = "admin@test.gov.in") -> dict:
    return headers(apiclient.admin(email)["access_token"])


def _entries(**filters) -> list:
    """The audit trail, newest first. ``limit`` is the store's maximum."""
    rows, _ = store.audit.list_entries(limit=500, **filters)
    return rows


def _actions(**filters) -> list[str]:
    return [e.action for e in _entries(**filters)]


def _create(admin_h: dict, email: str, role: str = "officer",
            password: str = PASSWORD, name: str = "Made By Admin"):
    return client.post("/admin/users", headers=admin_h,
                       json={"email": email, "password": password,
                             "name": name, "role": role})


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #
def test_admin_routes_are_admin_only():
    reset_db()
    consumer_h = headers(apiclient.consumer()["access_token"])
    officer_h = headers(apiclient.officer()["access_token"])
    admin_h = _admin_h()
    for route in ADMIN_ROUTES:
        assert client.get(route).status_code == 401, route
        # 403 and not 404: for these routes the existence of the administration
        # surface is not a secret worth keeping, and a 404 would send an officer
        # hunting for a typo in a URL that is simply not theirs.
        assert client.get(route, headers=consumer_h).status_code == 403, route
        assert client.get(route, headers=officer_h).status_code == 403, route
        assert client.get(route, headers=admin_h).status_code == 200, route


def test_the_refusal_names_the_role_that_was_refused():
    reset_db()
    officer_h = headers(apiclient.officer()["access_token"])
    detail = client.get("/admin/users", headers=officer_h).json()["detail"]
    assert "officer" in detail.lower() and "manage users" in detail.lower(), detail


# --------------------------------------------------------------------------- #
# Listing accounts
# --------------------------------------------------------------------------- #
def test_the_listing_counts_every_role_over_the_whole_table():
    reset_db()
    apiclient.consumer("c1@test.in")
    apiclient.consumer("c2@test.in")
    apiclient.officer("o1@test.gov.in")
    admin_h = _admin_h()
    body = client.get("/admin/users?limit=1", headers=admin_h).json()
    assert len(body["items"]) == 1
    assert body["total"] == 4
    # by_role is over the table, not the page — otherwise the dashboard number would
    # change depending on how the listing happened to be paged.
    assert body["by_role"] == {"consumer": 2, "officer": 1, "admin": 1}, body["by_role"]


def test_the_listing_never_carries_a_password_verifier():
    reset_db()
    admin_h = _admin_h()
    apiclient.consumer()
    raw = client.get("/admin/users", headers=admin_h).text.lower()
    for leak in ("password", "verifier", "pbkdf2", "hash", "salt"):
        assert leak not in raw, f"{leak!r} appears in the admin listing"


def test_the_listing_reports_each_accounts_inspection_count():
    reset_db()
    consumer = apiclient.consumer()
    apiclient.scan_id(consumer["access_token"], product_name="One")
    apiclient.scan_id(consumer["access_token"], product_name="Two")
    admin_h = _admin_h()
    items = client.get("/admin/users", headers=admin_h).json()["items"]
    counts = {i["email"]: i["scans"] for i in items}
    assert counts["consumer@test.in"] == 2, counts
    assert counts["admin@test.gov.in"] == 0, counts


def test_the_listing_filters_by_role_and_search():
    reset_db()
    apiclient.officer("inspector.rao@test.gov.in")
    apiclient.consumer("shopper@test.in")
    admin_h = _admin_h()
    only_officers = client.get("/admin/users?role=officer", headers=admin_h).json()
    assert [i["email"] for i in only_officers["items"]] == ["inspector.rao@test.gov.in"]
    found = client.get("/admin/users?search=rao", headers=admin_h).json()
    assert [i["email"] for i in found["items"]] == ["inspector.rao@test.gov.in"]


# --------------------------------------------------------------------------- #
# Creating accounts
# --------------------------------------------------------------------------- #
def test_a_created_account_can_sign_in_at_the_role_it_was_given():
    reset_db()
    admin_h = _admin_h()
    r = _create(admin_h, "new.officer@test.gov.in", "officer")
    assert r.status_code == 201, r.text
    assert r.json()["role"] == "officer"
    # The point of the endpoint: enrolling an inspector without handing them the
    # shared officer code, and without shell access to the host.
    login = client.post("/auth/login", json={"email": "new.officer@test.gov.in",
                                             "password": PASSWORD})
    assert login.status_code == 200, login.text
    assert login.json()["user"]["role"] == "officer"
    scans = client.get("/scans", headers=headers(login.json()["access_token"]))
    assert scans.json()["scope"] == "all"


def test_an_admin_can_be_created_here_but_still_not_self_granted():
    reset_db()
    admin_h = _admin_h()
    assert _create(admin_h, "second.admin@test.gov.in", "admin").status_code == 201
    # Sign-up remains closed to it: no enrolment code mints an administrator, so the
    # only two ways to get one are this endpoint and manage.py — both of which
    # presuppose somebody already holds the role.
    r = client.post("/auth/register", json={
        "email": "self.made@test.in", "password": PASSWORD, "name": "Nope",
        "role": "admin", "officer_code": apiclient.OFFICER_CODE})
    assert r.status_code in (400, 403, 422), r.text
    assert store.get_user_by_email("self.made@test.in") is None


def test_a_duplicate_email_is_a_conflict():
    reset_db()
    admin_h = _admin_h()
    apiclient.consumer("taken@test.in")
    r = _create(admin_h, "taken@test.in")
    assert r.status_code == 409, r.text
    # An admin can already list every account, so naming the collision leaks nothing
    # they could not read directly — and "already registered" is the actionable answer.
    assert "already registered" in r.json()["detail"]


def test_a_short_password_is_refused_before_the_account_exists():
    reset_db()
    admin_h = _admin_h()
    r = _create(admin_h, "weak@test.gov.in", password="short")
    assert r.status_code == 422, r.text
    assert store.get_user_by_email("weak@test.gov.in") is None


def test_a_mistyped_role_is_refused_not_silently_downgraded():
    reset_db()
    admin_h = _admin_h()
    r = _create(admin_h, "typo@test.gov.in", role="offcier")
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    # The message has to carry the valid set, because the whole failure mode is that
    # the admin cannot see what they got wrong.
    for role in Role:
        assert role.value in detail, detail
    # And nothing was created as a consumer behind their back.
    assert store.get_user_by_email("typo@test.gov.in") is None


def test_a_mistyped_role_filter_is_also_refused():
    reset_db()
    admin_h = _admin_h()
    r = client.get("/admin/users?role=officerr", headers=admin_h)
    # Leniency here would silently list consumers under the heading "officers", which
    # is a worse answer than an error.
    assert r.status_code == 422, r.text


# --------------------------------------------------------------------------- #
# Changing accounts
# --------------------------------------------------------------------------- #
def test_a_promotion_takes_effect_on_the_next_request():
    reset_db()
    consumer = apiclient.consumer()
    token = consumer["access_token"]
    assert client.get("/scans", headers=headers(token)).json()["scope"] == "own"
    admin_h = _admin_h()
    r = client.patch(f"/admin/users/{consumer['user']['id']}",
                     headers=admin_h, json={"role": "officer"})
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "officer"
    # Same token, no re-login. Authority is re-read from the database per request, so
    # a promotion does not wait out a twelve-hour token lifetime.
    assert client.get("/scans", headers=headers(token)).json()["scope"] == "all"


def test_disabling_an_account_revokes_its_token_at_once():
    reset_db()
    victim = apiclient.consumer("gone@test.in")
    token = victim["access_token"]
    admin_h = _admin_h()
    r = client.patch(f"/admin/users/{victim['user']['id']}",
                     headers=admin_h, json={"disabled": True})
    assert r.status_code == 200 and r.json()["disabled"] is True, r.text
    # The token was valid a moment ago and is signed for twelve hours. It stops working
    # now, because authority is re-read per request rather than trusted from the token.
    revoked = client.get("/scans", headers=headers(token))
    assert revoked.status_code == 403
    assert "disabled" in revoked.json()["detail"].lower()

    # Logging in again gives the same 401 as a wrong password, on purpose: /auth/login
    # must not become an oracle for which addresses are registered.
    refused = client.post("/auth/login", json={"email": "gone@test.in",
                                               "password": PASSWORD})
    unknown = client.post("/auth/login", json={"email": "never@test.in",
                                               "password": PASSWORD})
    assert refused.status_code == unknown.status_code == 401
    assert refused.json() == unknown.json()


def test_a_patch_that_changes_nothing_is_refused():
    reset_db()
    target = apiclient.consumer()
    admin_h = _admin_h()
    r = client.patch(f"/admin/users/{target['user']['id']}", headers=admin_h, json={})
    assert r.status_code == 422, r.text
    assert "role" in r.json()["detail"] and "disabled" in r.json()["detail"]


def test_patching_an_unknown_account_is_404():
    reset_db()
    admin_h = _admin_h()
    r = client.patch("/admin/users/does-not-exist", headers=admin_h,
                     json={"role": "officer"})
    assert r.status_code == 404, r.text


def test_re_disabling_an_already_disabled_account_is_a_no_op():
    reset_db()
    target = apiclient.consumer("idle@test.in")
    admin_h = _admin_h()
    path = f"/admin/users/{target['user']['id']}"
    assert client.patch(path, headers=admin_h, json={"disabled": True}).status_code == 200
    before = len(_actions(action=store.audit.USER_DISABLE))
    assert client.patch(path, headers=admin_h, json={"disabled": True}).status_code == 200
    # A PATCH that asks for the state the account is already in must not manufacture an
    # audit entry: the log answers "what changed", and a row for a no-op is noise that
    # makes a real second change harder to find.
    assert len(_actions(action=store.audit.USER_DISABLE)) == before


# --------------------------------------------------------------------------- #
# Lockout
# --------------------------------------------------------------------------- #
def test_the_last_admin_cannot_lock_the_server_out():
    reset_db()
    admin = apiclient.admin()
    admin_h = headers(admin["access_token"])
    path = f"/admin/users/{admin['user']['id']}"
    for body in ({"role": "officer"}, {"disabled": True}, {"role": "consumer",
                                                           "disabled": True}):
        r = client.patch(path, headers=admin_h, json=body)
        assert r.status_code == 409, (body, r.status_code, r.text)
        assert "only administrator" in r.json()["detail"]
    # Still an admin, still able to sign in — the refusal has to be complete, not
    # partial. A PATCH that applied the role change and then refused the disable would
    # be the worst of both.
    me = client.get("/auth/me", headers=admin_h)
    assert me.status_code == 200 and me.json()["role"] == "admin", me.text


def test_a_second_admin_makes_the_first_demotable():
    reset_db()
    first = apiclient.admin("first@test.gov.in")
    admin_h = headers(first["access_token"])
    assert _create(admin_h, "second@test.gov.in", "admin").status_code == 201
    r = client.patch(f"/admin/users/{first['user']['id']}",
                     headers=admin_h, json={"role": "officer"})
    assert r.status_code == 200, r.text
    # And the demotion applies to the account that just performed it: the next request
    # with the same token is an officer's.
    assert client.get("/admin/users", headers=admin_h).status_code == 403


def test_a_disabled_admin_does_not_count_as_cover():
    reset_db()
    first = apiclient.admin("first@test.gov.in")
    admin_h = headers(first["access_token"])
    second = _create(admin_h, "second@test.gov.in", "admin").json()
    assert client.patch(f"/admin/users/{second['id']}", headers=admin_h,
                        json={"disabled": True}).status_code == 200
    # An account that cannot sign in cannot recover the server, so it is not somebody
    # still holding the keys.
    r = client.patch(f"/admin/users/{first['user']['id']}",
                     headers=admin_h, json={"role": "consumer"})
    assert r.status_code == 409, r.text


# --------------------------------------------------------------------------- #
# The audit trail: what gets recorded
# --------------------------------------------------------------------------- #
def test_every_account_change_is_audited_with_both_ends_of_it():
    reset_db()
    admin_h = _admin_h()
    made = _create(admin_h, "audited@test.gov.in", "consumer").json()
    client.patch(f"/admin/users/{made['id']}", headers=admin_h, json={"role": "officer"})
    client.patch(f"/admin/users/{made['id']}", headers=admin_h, json={"disabled": True})

    created = _entries(action=store.audit.USER_CREATE)
    assert len(created) == 1 and created[0].target == made["id"]
    assert created[0].detail["email"] == "audited@test.gov.in"

    role = _entries(action=store.audit.USER_ROLE)
    assert len(role) == 1 and role[0].target == made["id"]
    # Both ends, not just the new value: "was made an officer" is a much weaker record
    # than "was a consumer and was made an officer".
    assert role[0].detail["from"] == "consumer" and role[0].detail["to"] == "officer"
    assert role[0].detail["self"] is False

    disabled = _entries(action=store.audit.USER_DISABLE)
    assert len(disabled) == 1 and disabled[0].detail["disabled"] is True


def test_the_actor_is_recorded_with_the_role_they_held_at_the_time():
    reset_db()
    first = apiclient.admin("first@test.gov.in")
    admin_h = headers(first["access_token"])
    _create(admin_h, "second@test.gov.in", "admin")
    entry_before = _entries(action=store.audit.USER_CREATE)[0]
    assert entry_before.actor_role == "admin"

    # Now demote the actor. Their old entry must not be rewritten: the question the log
    # answers is what this person was allowed to do *then*.
    client.patch(f"/admin/users/{first['user']['id']}", headers=admin_h,
                 json={"role": "consumer"})
    entry_after = _entries(action=store.audit.USER_CREATE)[0]
    assert entry_after.id == entry_before.id
    assert entry_after.actor_role == "admin"
    assert entry_after.actor_email == "first@test.gov.in"
    assert store.get_user(first["user"]["id"]).role is Role.CONSUMER


def test_the_entry_survives_the_actors_account():
    reset_db()
    admin_h = _admin_h()
    admin_id = client.get("/auth/me", headers=admin_h).json()["id"]
    _create(admin_h, "witness@test.gov.in", "officer")
    entry = _entries(action=store.audit.USER_CREATE)[0]
    assert entry.actor_id == admin_id

    # Straight SQL on purpose. ``store`` has no ``delete_user`` — accounts are disabled,
    # not destroyed — so what is under test here is the schema's own contract
    # (``actor_id`` is ``ON DELETE SET NULL``) rather than any code path above it.
    store.db.execute("DELETE FROM users WHERE id = ?", (admin_id,))
    assert store.get_user(admin_id) is None

    after = _entries(action=store.audit.USER_CREATE)[0]
    # The row survives with actor_id nulled, and the frozen email and role remain — so
    # removing the account cannot erase what it did.
    assert after.id == entry.id
    assert after.actor_id is None
    assert after.actor_email == "admin@test.gov.in"
    assert after.actor_role == "admin"


def test_a_rulepack_reload_is_audited():
    reset_db()
    admin_h = _admin_h()
    r = client.post("/reload", headers=admin_h)
    assert r.status_code == 200, r.text
    entries = _entries(action=store.audit.PACKS_RELOAD)
    assert len(entries) == 1
    # Replacing the rules every verdict is measured against is the single most
    # consequential thing an admin can do, so the entry records which versions landed.
    assert entries[0].detail["packs_loaded"] == r.json()["packs_loaded"]
    assert "legal_metrology_2011" in entries[0].detail["packs"]


# --------------------------------------------------------------------------- #
# The audit trail: what does NOT get recorded
# --------------------------------------------------------------------------- #
def test_a_consumers_own_reads_are_not_events_but_an_officers_are():
    reset_db()
    consumer = apiclient.consumer()
    apiclient.scan_id(consumer["access_token"], product_name="Mine")
    consumer_h = headers(consumer["access_token"])
    for path in ("/scans", "/stats", "/scans.csv"):
        assert client.get(path, headers=consumer_h).status_code == 200, path
    # Nothing. A consumer paging their own history is ordinary traffic, and logging it
    # would bury the reads that matter.
    assert _actions() == []

    officer_h = headers(apiclient.officer()["access_token"])
    for path in ("/scans", "/stats", "/scans.csv"):
        assert client.get(path, headers=officer_h).status_code == 200, path
    assert sorted(_actions()) == sorted([
        store.audit.CORPUS_LIST, store.audit.CORPUS_STATS, store.audit.CORPUS_EXPORT,
    ])


def test_opening_someone_elses_inspection_is_recorded_and_your_own_is_not():
    reset_db()
    consumer = apiclient.consumer()
    mine = apiclient.scan_id(consumer["access_token"], product_name="Mine")
    assert client.get(f"/scans/{mine}",
                      headers=headers(consumer["access_token"])).status_code == 200
    assert _actions(action=store.audit.CORPUS_READ) == []

    officer_h = headers(apiclient.officer()["access_token"])
    assert client.get(f"/scans/{mine}", headers=officer_h).status_code == 200
    entries = _entries(action=store.audit.CORPUS_READ)
    assert len(entries) == 1
    assert entries[0].target == mine
    assert entries[0].detail["owner_id"] == consumer["user"]["id"]


def test_a_refused_delete_leaves_no_trace():
    reset_db()
    owner = apiclient.consumer("owner@test.in")
    theirs = apiclient.scan_id(owner["access_token"], product_name="Not Yours")
    intruder_h = headers(apiclient.consumer("intruder@test.in")["access_token"])

    assert client.delete(f"/scans/{theirs}", headers=intruder_h).status_code == 404
    assert client.delete("/scans/no-such-id", headers=intruder_h).status_code == 404
    # The log records what happened, not what was attempted. An entry here would say an
    # inspection was deleted when it still exists, which is worse than no entry at all.
    assert _actions(action=store.audit.SCAN_DELETE) == []
    assert store.get_scan(theirs) is not None


def test_a_delete_is_described_from_the_row_before_it_goes():
    reset_db()
    owner = apiclient.consumer("owner@test.in")
    doomed = apiclient.scan_id(owner["access_token"], product_name="Haldiram Bhujia")
    admin_h = _admin_h()
    assert client.delete(f"/scans/{doomed}", headers=admin_h).status_code == 204

    entries = _entries(action=store.audit.SCAN_DELETE)
    assert len(entries) == 1
    detail = entries[0].detail
    # "deleted <uuid>" is not a record of what was lost. The row is read first so the
    # entry can still say what it was, which is the only remaining description of it.
    assert entries[0].target == doomed
    assert detail["product_name"] == "Haldiram Bhujia"
    assert detail["verdict"] and detail["created_at"]
    assert detail["own"] is False and detail["owner_id"] == owner["user"]["id"]
    assert store.get_scan(doomed) is None


def test_minting_a_share_link_is_audited_for_everyone():
    reset_db()
    consumer = apiclient.consumer()
    mine = apiclient.scan_id(consumer["access_token"], product_name="Shared")
    r = client.post(f"/scans/{mine}/share?minutes=30",
                    headers=headers(consumer["access_token"]))
    assert r.status_code == 200, r.text
    entries = _entries(action=store.audit.REPORT_SHARE)
    # Recorded even though the consumer is sharing their own report and gains nothing:
    # a link that opens a report with no login is a bearer credential, and how long it
    # lives is worth knowing later.
    assert len(entries) == 1
    assert entries[0].target == mine
    assert entries[0].detail["minutes"] == 30
    assert entries[0].detail["own"] is True


def test_the_recorded_address_comes_from_the_forwarded_header():
    reset_db()
    officer_h = headers(apiclient.officer()["access_token"])
    officer_h["X-Forwarded-For"] = "203.0.113.9, 10.0.0.1"
    assert client.get("/scans", headers=officer_h).status_code == 200
    entry = _entries(action=store.audit.CORPUS_LIST)[0]
    # The left-most entry is the client; the rest are proxies. Best effort by nature,
    # which is why it is in the audit detail and never used for authorisation.
    assert entry.detail["ip"] == "203.0.113.9"


# --------------------------------------------------------------------------- #
# Reading the audit trail over HTTP
# --------------------------------------------------------------------------- #
def test_the_trail_reads_newest_first_and_filters_compose():
    reset_db()
    consumer = apiclient.consumer()
    apiclient.scan_id(consumer["access_token"], product_name="For The Log")
    officer_h = headers(apiclient.officer()["access_token"])
    client.get("/scans", headers=officer_h)
    client.get("/stats", headers=officer_h)
    admin_h = _admin_h()
    officer_id = store.get_user_by_email("officer@test.gov.in").id
    _create(admin_h, "extra@test.gov.in", "officer")

    body = client.get("/admin/audit", headers=admin_h).json()
    assert body["total"] == 3
    stamps = [e["created_at"] for e in body["items"]]
    assert stamps == sorted(stamps, reverse=True)

    by_action = client.get(f"/admin/audit?action={store.audit.CORPUS_STATS}",
                           headers=admin_h).json()
    assert [e["action"] for e in by_action["items"]] == [store.audit.CORPUS_STATS]

    by_actor = client.get(f"/admin/audit?actor_id={officer_id}", headers=admin_h).json()
    assert by_actor["total"] == 2
    assert {e["actor_email"] for e in by_actor["items"]} == {"officer@test.gov.in"}

    both = client.get(f"/admin/audit?actor_id={officer_id}"
                      f"&action={store.audit.CORPUS_LIST}", headers=admin_h).json()
    assert both["total"] == 1


def test_reading_the_trail_is_not_itself_audited():
    reset_db()
    admin_h = _admin_h()
    _create(admin_h, "one@test.gov.in", "officer")
    before = client.get("/admin/audit", headers=admin_h).json()["total"]
    for _ in range(3):
        client.get("/admin/audit", headers=admin_h)
    # A deliberate stopping point: logging reads of the log invites an unbounded
    # regress. The protection that matters is that entries cannot be removed.
    assert client.get("/admin/audit", headers=admin_h).json()["total"] == before


def test_the_trail_is_append_only_over_http():
    reset_db()
    admin_h = _admin_h()
    for method in (client.delete, client.put, client.post, client.patch):
        r = method("/admin/audit", headers=admin_h)
        assert r.status_code == 405, (method.__name__, r.status_code)
    # Retention exists, but only from the shell: `manage.py audit --purge-before`.
    # A log the API it records can erase is not evidence.
    assert "/admin/audit" in client.get("/openapi.json").json()["paths"]
    assert set(client.get("/openapi.json").json()["paths"]["/admin/audit"]) == {"get"}


def test_the_admin_surface_needs_a_database():
    reset_db()
    admin_h = _admin_h()
    import os

    from app import deps
    os.environ[deps.PERSISTENCE_DISABLED_ENV] = "1"
    try:
        deps.init_persistence()
        assert deps.persistence_ready() is False
        # 503, not 500 and not a bare 401: accounts are a degradation on a box that
        # runs the engine only, and the answer should say so.
        for route in ADMIN_ROUTES:
            r = client.get(route, headers=admin_h)
            assert r.status_code in (401, 503), (route, r.status_code)
    finally:
        os.environ.pop(deps.PERSISTENCE_DISABLED_ENV, None)
        reset_db()


# --------------------------------------------------------------------------- #
# Self-contained runner (no pytest required)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    raise SystemExit(apiclient.run_all(globals(), title="admin & audit tests"))
