#!/usr/bin/env python3
"""
Tests for the persistence layer (``store``): schema, scan history, and users.

Runs two ways:
    pytest                       # from the backend/ directory
    python3 tests/test_store.py  # no pytest needed — self-contained runner

Every test runs against a fresh ``:memory:`` database, so real inspection history is
never touched and the order tests run in cannot matter.

The guarantees worth stating outright:

* ``test_report_is_stored_verbatim`` — the stored report must be byte-for-byte the one
  the officer was shown. It is the audit record; a lossy round-trip would mean the
  printed document could disagree with what the tool actually decided.
* ``test_save_scan_survives_a_malformed_report`` — losing history is always preferable
  to failing a scan, so a report missing keys must still store.
* ``test_no_label_reads_are_excluded_from_averages`` — a photo that was not a label is
  not a product scoring zero, and must not drag the compliance rate down.
* ``test_password_verifier_never_leaves_the_store`` — ``User.to_dict()`` feeds the API
  response model directly, so a verifier appearing there would ship to clients.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
SAMPLES = BACKEND / "samples"
sys.path.insert(0, str(BACKEND))

import store  # noqa: E402
from auth.roles import Role  # noqa: E402
from rule_engine import ScanInput, evaluate_scan  # noqa: E402


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def _fresh_db() -> None:
    """Point the store at a brand-new in-memory database."""
    store.configure(":memory:")
    store.init_schema(store.connection())


def _report(sample: str = "good_label.json", **overrides) -> dict:
    raw = json.loads((SAMPLES / sample).read_text(encoding="utf-8"))
    raw.update(overrides)
    return evaluate_scan(ScanInput.from_dict(raw)).to_dict()


def _user(email: str = "a@b.in", role: Role = Role.CONSUMER):
    return store.create_user(email, "password123", name="Test", role=role)


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #
def test_schema_initialises_and_is_idempotent():
    _fresh_db()
    first = store.init_schema(store.connection())
    second = store.init_schema(store.connection())
    assert first == second == store.SCHEMA_VERSION
    stats = store.db_stats()
    assert stats["schema_version"] == store.SCHEMA_VERSION
    assert stats["scans"] == 0 and stats["users"] == 0


def test_journal_mode_is_reported():
    """An in-memory database has no journal to configure; the field must still exist
    so ``/health`` and ``manage.py stats`` can render without a KeyError."""
    _fresh_db()
    assert "journal_mode" in store.db_stats()


# --------------------------------------------------------------------------- #
# Scans
# --------------------------------------------------------------------------- #
def test_save_and_get_scan_round_trip():
    _fresh_db()
    u = _user()
    row = store.save_scan(_report(), user_id=u.id, product_name="Biscuits",
                          note="market check", location="Pune")
    assert row.id and row.user_id == u.id
    got = store.get_scan(row.id)
    assert got is not None
    assert got.product_name == "Biscuits"
    assert got.note == "market check" and got.location == "Pune"
    assert got.verdict == row.verdict and got.score == row.score


def test_report_is_stored_verbatim():
    """The stored report is the audit record. It must survive the round trip exactly."""
    _fresh_db()
    payload = _report("bad_label.json")
    row = store.save_scan(payload, scan_input={"category": "packaged_food"})
    got = store.get_scan(row.id)
    assert got.report == payload, "stored report differs from the one that was judged"
    assert got.scan_input == {"category": "packaged_food"}


def test_list_view_omits_the_report_body():
    """List views must not deserialise every report — that is the whole point of the
    denormalised columns. A populated ``report`` here would mean /scans got slower in
    proportion to how much history exists."""
    _fresh_db()
    store.save_scan(_report())
    rows, total = store.list_scans()
    assert total == 1
    assert rows[0].report is None
    assert rows[0].verdict and rows[0].score > 0  # summary data still present


def test_scans_are_scoped_by_user():
    _fresh_db()
    a, b = _user("a@x.in"), _user("b@x.in")
    store.save_scan(_report(), user_id=a.id)
    store.save_scan(_report(), user_id=b.id)
    store.save_scan(_report())  # anonymous

    everything, total_all = store.list_scans()
    assert total_all == 3, "unscoped list is the officer's view: it sees all three"

    mine, total_mine = store.list_scans(user_id=a.id)
    assert total_mine == 1 and mine[0].user_id == a.id

    # An anonymous scan belongs to nobody, so it must not appear in anybody's history.
    assert all(r.user_id != b.id for r in mine)


def test_get_scan_respects_the_user_filter():
    """The 404-not-403 behaviour the API relies on starts here."""
    _fresh_db()
    a, b = _user("a@x.in"), _user("b@x.in")
    row = store.save_scan(_report(), user_id=a.id)
    assert store.get_scan(row.id, user_id=a.id) is not None
    assert store.get_scan(row.id, user_id=b.id) is None


def test_delete_scan_is_scoped_and_cascades():
    _fresh_db()
    a, b = _user("a@x.in"), _user("b@x.in")
    row = store.save_scan(_report("bad_label.json"), user_id=a.id)
    violations = store.db.query_one(
        "SELECT COUNT(*) AS n FROM scan_violations WHERE scan_id = ?", (row.id,))["n"]
    assert violations > 0, "the bad label should have exploded into violation rows"

    assert store.delete_scan(row.id, user_id=b.id) is False, "not b's scan to delete"
    assert store.get_scan(row.id) is not None
    assert store.delete_scan(row.id, user_id=a.id) is True
    assert store.get_scan(row.id) is None
    left = store.db.query_one(
        "SELECT COUNT(*) AS n FROM scan_violations WHERE scan_id = ?", (row.id,))["n"]
    assert left == 0, "violation rows must cascade with their parent scan"


def test_list_filters_compose():
    _fresh_db()
    store.save_scan(_report(), product_name="Tata Salt", location="Pune")
    store.save_scan(_report("bad_label.json"), product_name="Toffee Jar",
                    location="Nashik")

    _, n = store.list_scans(verdict="compliant")
    assert n == 1
    _, n = store.list_scans(search="Toffee")
    assert n == 1
    _, n = store.list_scans(search="Nashik")           # searches location too
    assert n == 1
    _, n = store.list_scans(verdict="compliant", search="Toffee")
    assert n == 0, "filters must AND together, not OR"


def test_pagination_reports_the_unpaged_total():
    _fresh_db()
    for i in range(5):
        store.save_scan(_report(), product_name=f"Item {i}")
    rows, total = store.list_scans(limit=2, offset=0)
    assert len(rows) == 2 and total == 5, "total is the match count, not the page size"
    page2, _ = store.list_scans(limit=2, offset=2)
    assert {r.id for r in rows}.isdisjoint({r.id for r in page2})


def test_save_scan_survives_a_malformed_report():
    """Storage is tolerant on purpose: a missing key must degrade, not raise, because
    losing the history entry is a smaller harm than failing the scan that produced it."""
    _fresh_db()
    row = store.save_scan({"verdict": "needs_review"})   # no score, summary, violations
    assert row.id
    got = store.get_scan(row.id)
    assert got.verdict == "needs_review"
    assert got.score == 0 and got.packs_applied == []
    assert store.save_scan({}).id, "even a completely empty report must store"


def test_provenance_flags_are_preserved():
    """``mock`` records that a report was built from canned values. If it silently
    defaulted to False, a synthetic read would be filed as a real optical one."""
    _fresh_db()
    row = store.save_scan(_report(), source="image", mock=True)
    got = store.get_scan(row.id)
    assert got.source == "image" and got.mock is True


# --------------------------------------------------------------------------- #
# Aggregates
# --------------------------------------------------------------------------- #
def test_aggregate_stats_counts_and_averages():
    _fresh_db()
    store.save_scan(_report())                       # compliant, 100
    store.save_scan(_report("bad_label.json"))       # non_compliant, low
    data = store.aggregate_stats()
    assert data["total_scans"] == 2
    assert data["by_verdict"]["compliant"] == 1
    assert 0 < data["average_score"] < 100
    assert data["violations_total"] > 0
    assert set(data["violations_by_severity"]) >= {"critical", "major", "minor"}


def test_no_label_reads_are_excluded_from_averages():
    """A photograph that was not a label at all is not a product scoring zero."""
    _fresh_db()
    store.save_scan(_report())                                  # compliant, 100
    store.save_scan({"verdict": "no_label_detected", "score": 0})
    data = store.aggregate_stats()
    assert data["total_scans"] == 2
    assert data["scored_scans"] == 1, "the non-label read must not be scored"
    assert data["average_score"] == 100.0, (
        f"average dragged to {data['average_score']} by a non-label read")
    assert data["compliance_rate"] == 100.0


def test_top_violations_ranks_by_frequency():
    _fresh_db()
    for _ in range(3):
        store.save_scan(_report("bad_label.json"))
    top = store.top_violations(limit=5)
    assert top, "a bad label should produce ranked violations"
    counts = [v["occurrences"] for v in top]
    assert counts == sorted(counts, reverse=True), "must be worst-first"
    first = top[0]
    assert first["scans_affected"] == 3
    assert first["legal_reference"], "every ranked violation must cite its rule"


def test_stats_are_scoped_by_user():
    _fresh_db()
    a, b = _user("a@x.in"), _user("b@x.in")
    store.save_scan(_report(), user_id=a.id)
    store.save_scan(_report("bad_label.json"), user_id=b.id)
    assert store.aggregate_stats(user_id=a.id)["total_scans"] == 1
    assert store.aggregate_stats()["total_scans"] == 2


# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #
def test_create_user_normalises_email_and_defaults_to_consumer():
    _fresh_db()
    u = store.create_user("  MiXeD@Example.IN  ", "password123")
    assert u.email == "mixed@example.in", "email must be stored lower-cased and trimmed"
    assert u.role is Role.CONSUMER, "the default role must be the least privileged one"
    assert store.get_user_by_email("MIXED@EXAMPLE.IN") is not None, "lookup is case-insensitive"


def test_duplicate_email_is_rejected():
    _fresh_db()
    _user("dup@x.in")
    try:
        _user("DUP@x.in")
    except store.UserExists:
        return
    raise AssertionError("a duplicate email must raise UserExists, case-insensitively")


def test_create_user_rejects_a_nonsense_address():
    _fresh_db()
    for bad in ("", "   ", "no-at-sign"):
        try:
            store.create_user(bad, "password123")
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} should not be accepted as an email address")


def test_authenticate_accepts_correct_and_rejects_wrong():
    _fresh_db()
    _user("who@x.in")
    assert store.authenticate("who@x.in", "password123") is not None
    assert store.authenticate("WHO@x.in", "password123") is not None
    assert store.authenticate("who@x.in", "wrong") is None
    assert store.authenticate("nobody@x.in", "password123") is None


def test_password_verifier_never_leaves_the_store():
    """``User.to_dict()`` is what the API's response model receives. A verifier
    reachable from here would be one serialisation away from shipping to clients."""
    _fresh_db()
    u = _user()
    payload = u.to_dict()
    assert "password_hash" not in payload
    blob = json.dumps(payload).lower()
    for token in ("pbkdf2", "password_hash", "$"):
        assert token not in blob, f"{token!r} leaked into User.to_dict()"


def test_disabled_account_cannot_authenticate():
    _fresh_db()
    u = _user("off@x.in")
    assert store.set_disabled(u.id, True) is True
    assert store.authenticate("off@x.in", "password123") is None, (
        "a disabled account must not be able to log in")
    store.set_disabled(u.id, False)
    assert store.authenticate("off@x.in", "password123") is not None


def test_set_role_changes_privilege():
    _fresh_db()
    u = _user("promote@x.in")
    assert store.set_role(u.id, Role.OFFICER) is True
    assert store.get_user(u.id).role is Role.OFFICER
    assert store.set_role("no-such-id", Role.ADMIN) is False


def test_deleting_a_user_leaves_their_scans_as_anonymous():
    """``ON DELETE SET NULL``: an inspection is evidence and outlives the account that
    filed it. Cascading it away would let removing a user erase the record."""
    _fresh_db()
    u = _user("leaver@x.in")
    row = store.save_scan(_report(), user_id=u.id)
    store.db.execute("DELETE FROM users WHERE id = ?", (u.id,))
    survived = store.get_scan(row.id)
    assert survived is not None, "deleting a user must not delete their inspections"
    assert survived.user_id is None


def test_list_users_and_count():
    _fresh_db()
    for i in range(3):
        _user(f"u{i}@x.in")
    assert store.count_users() == 3
    assert len(store.list_users(limit=2)) == 2
    assert len(store.list_users(limit=10, offset=2)) == 1


# --------------------------------------------------------------------------- #
# Self-contained runner (no pytest required)
# --------------------------------------------------------------------------- #
def _run_all() -> int:
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
        except Exception as e:  # noqa: BLE001
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed, {len(tests)} total")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    print(f"Running store tests (today = {date.today().isoformat()})\n")
    raise SystemExit(_run_all())
