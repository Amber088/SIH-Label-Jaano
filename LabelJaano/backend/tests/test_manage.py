#!/usr/bin/env python3
"""
Tests for the ``manage.py`` command line — the audit reader, the retention purge, and
the shell actor identity those two write with.

Runs two ways:
    pytest                        # from the backend/ directory
    python3 tests/test_manage.py  # no pytest needed — self-contained runner

Three operations are deliberately unreachable over HTTP and live here instead: minting
an administrator, resetting a password, and trimming the audit log. This module is about
the third and the identity the first two share, because those are the paths where a
mistake is least likely to be noticed — nobody watches a CLI the way they watch an API.

Unlike the other test modules this one cannot use ``:memory:``. ``manage.main`` closes
the store in a ``finally``, which for an in-memory database destroys it, so every
command would start from an empty schema and see none of the fixtures. Each test
therefore runs against a throwaway file under ``$TMPDIR`` and re-opens it to assert, and
:func:`_restore_memory_db` puts the shared in-memory configuration back afterwards — a
leaked file path would silently move the *other* modules' tests onto disk.

The headline guarantees:

* ``test_a_purge_on_a_large_log_counts_from_the_right_end`` — the regression this module
  was written for. ``list_entries`` is newest-first with no "before" filter, so counting
  a purge from its first page reports "nothing to do" about a deletion of thousands.
* ``test_the_purge_records_itself_and_the_record_survives`` — a gap in the dates with
  nothing explaining it is what makes a log stop being evidence.
* ``test_the_operator_is_recorded_as_a_shell_not_as_an_admin`` — "someone had a shell on
  the host" and "an account with the admin role did this" are different facts.
* ``test_a_date_is_normalised_rather_than_compared_as_typed`` — the string comparison is
  only chronological because the width is fixed, and ``2026-7-1`` is the input that
  breaks that assumption in the most damaging possible direction.
"""
from __future__ import annotations

import io
import json
import os
import sys
import uuid
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import apiclient  # noqa: E402
import manage  # noqa: E402
import store  # noqa: E402
from manage import CommandError, _parse_when  # noqa: E402

#: Where the throwaway databases go. Never the real ``data/labeljaano.db``: a test run
#: that touched it would leave rows the dev server then shows as demo history.
_TMP = Path(os.environ.get("TMPDIR", "/tmp"))

#: Every database this module created, so :func:`_restore_memory_db` can remove them.
#: Without this each run left twenty files behind in ``$TMPDIR`` forever — harmless
#: individually, and exactly the kind of thing nobody notices until a CI box fills up.
_MADE: list[Path] = []


def _fresh_db() -> str:
    """A new empty database file, configured and schema'd. Returns its path."""
    path = _TMP / f"labeljaano-test-{uuid.uuid4().hex[:12]}.db"
    _MADE.append(path)
    store.configure(str(path))
    store.init_schema(store.connection())
    return str(path)


def _restore_memory_db() -> None:
    """Put the shared in-memory configuration back, as the other modules expect it.

    Also deletes the files made so far. Safe here and only here: the store is closed
    first, and each test finishes with its own database in this ``finally``.
    """
    store.close()
    store.configure(":memory:")
    while _MADE:
        stem = _MADE.pop()
        # -wal and -shm too; SQLite may have left either alongside the database.
        for leftover in stem.parent.glob(stem.name + "*"):
            try:
                leftover.unlink()
            except OSError:      # still held open on some platforms; not worth failing
                pass


def _run(*argv: str, db: str) -> tuple[int, str]:
    """``manage.main`` with stdout captured. ``(exit_code, output)``.

    Called in-process rather than as a subprocess so a failure points at a line of
    ``manage.py`` instead of at a return code, and so ``$TMPDIR`` is the only thing the
    test has to clean up.

    Stdin is swapped for an empty buffer, which makes ``sys.stdin.isatty()`` false. That
    is what pytest already does, and doing it here as well is what keeps the standalone
    runner from hanging on a confirmation prompt at a real terminal.
    """
    buf = io.StringIO()
    real_stdin, sys.stdin = sys.stdin, io.StringIO()
    try:
        with redirect_stdout(buf):
            code = manage.main(["--db", db, *argv])
    finally:
        sys.stdin = real_stdin
    return code, buf.getvalue()


def _reopen(db: str) -> None:
    """``main`` closes the store on the way out; re-open it to assert against."""
    store.configure(db)


def _entries(db: str, **filters):
    _reopen(db)
    return store.audit.list_entries(limit=500, **filters)


def _all_actions(db: str) -> list[str]:
    """Every action in the log, oldest first — past ``list_entries``' 500-row cap."""
    _reopen(db)
    return [r["action"] for r in
            store.db.query("SELECT action FROM audit_log ORDER BY id ASC")]


def _seed_audit(db: str, count: int, *, action: str = "scans.export",
                age_days: int = 0) -> None:
    """*count* audit entries, optionally backdated by *age_days*.

    Retention is about age and the alternative to backdating is waiting, which is not a
    test. Inserted with SQL rather than through :func:`store.audit.record` when a date is
    wanted, because there is deliberately no API for setting or editing an entry's
    timestamp — the point of the table is that nothing rewrites it.
    """
    _reopen(db)
    stamp = ((datetime.now(timezone.utc) - timedelta(days=age_days))
             .isoformat(timespec="seconds").replace("+00:00", "Z"))
    conn = store.connection()
    conn.executemany(
        "INSERT INTO audit_log (created_at, actor_id, actor_email, actor_role,"
        " action, target, detail) VALUES (?, NULL, ?, 'officer', ?, ?, ?)",
        [(stamp, f"officer{n % 3}@test.gov.in", action, f"scan-{n:04d}",
          json.dumps({"n": n})) for n in range(count)],
    )
    conn.commit()


# --------------------------------------------------------------------------- #
# _parse_when — the string that a whole table is compared against
# --------------------------------------------------------------------------- #
def test_an_age_becomes_a_timestamp_in_the_past():
    """``30d`` / ``24h`` / ``90m`` are the forms an operator actually types."""
    now = datetime.now(timezone.utc)
    for spec, delta in (("90m", timedelta(minutes=90)),
                        ("24h", timedelta(hours=24)),
                        ("30d", timedelta(days=30)),
                        ("1d", timedelta(days=1)),
                        ("365d", timedelta(days=365))):
        got = _parse_when(spec)
        assert got.endswith("Z"), got
        parsed = datetime.fromisoformat(got.replace("Z", "+00:00"))
        assert abs((now - delta) - parsed) < timedelta(seconds=5), (spec, got)
        # Uppercase is the same age: nobody should have to know the unit is lowercase.
        upper = datetime.fromisoformat(_parse_when(spec.upper()).replace("Z", "+00:00"))
        assert abs(upper - parsed) < timedelta(seconds=5), spec


def test_a_date_is_normalised_rather_than_compared_as_typed():
    """``2026-7-1`` comes back as ``2026-07-01``, and that matters more than it looks.

    The cutoff is compared lexicographically against ``created_at``, which is
    chronological *only* because every stored timestamp is fixed-width UTC ISO-8601.
    ``"2026-7-1"`` breaks that: it sorts after every real timestamp of that year, so
    passed through verbatim it would match nothing as a ``--since`` and — far worse —
    everything as a ``--purge-before``. Round-tripping through ``strptime`` is what stops
    a mistyped date from emptying the evidence log.
    """
    assert _parse_when("2026-07-01") == "2026-07-01"
    assert _parse_when("2026-7-1") == "2026-07-01"
    assert _parse_when("2026-7-01") == "2026-07-01"
    assert _parse_when("  2026-08-01  ") == "2026-08-01"

    # The property the normalisation exists for, stated directly.
    assert "2026-7-1" > "2026-12-31T23:59:59Z", "premise of this test no longer holds"
    assert _parse_when("2026-7-1") < "2026-12-31T23:59:59Z"


def test_a_full_timestamp_keeps_its_time_and_gains_a_z():
    """Both ISO spellings are accepted and normalised to the stored form."""
    assert _parse_when("2026-08-01T09:30:00") == "2026-08-01T09:30:00Z"
    assert _parse_when("2026-08-01T09:30:00Z") == "2026-08-01T09:30:00Z"
    assert _parse_when("2026-08-01 09:30:00") == "2026-08-01T09:30:00Z"
    # Same instant, same string, whichever way it was typed.
    assert len({_parse_when(s) for s in ("2026-08-01T09:30:00",
                                        "2026-08-01 09:30:00",
                                        "2026-08-01T09:30:00Z")}) == 1


def test_something_that_is_not_a_time_is_refused_not_guessed():
    """A cutoff nobody can read is a deletion nobody can predict.

    Every one of these would otherwise be compared as a raw string against the whole
    table, so the failure would be a silent purge of the wrong range rather than an error.
    """
    for junk in ("", "   ", "0d", "-5d", "30x", "d", "last tuesday", "yesterday",
                 "2026-13-01", "2026-02-30", "01-08-2026", "2026/08/01", "1e3d",
                 "3.5d", "тридцать", "2026-08-01T25:00:00"):
        try:
            got = _parse_when(junk)
        except CommandError:
            continue
        raise AssertionError(f"{junk!r} was accepted as {got!r}")


def test_the_refusal_says_what_would_have_worked():
    """The error carries the three accepted forms, because a bare "invalid" does not help.

    An operator meets this message while trying to enact a retention period, which is not
    the moment to go and read the source.
    """
    for junk in ("last tuesday", "0d"):
        try:
            _parse_when(junk)
        except CommandError as exc:
            message = str(exc)
        else:
            raise AssertionError(junk)
        assert junk in message, message
    try:
        _parse_when("nonsense")
    except CommandError as exc:
        message = str(exc)
    else:
        raise AssertionError("'nonsense' was accepted")
    assert "30d" in message and "2026-08-01" in message, message


# --------------------------------------------------------------------------- #
# The purge
# --------------------------------------------------------------------------- #
def test_a_purge_on_a_large_log_counts_from_the_right_end():
    """The regression this module exists for.

    ``store.audit.list_entries`` is newest-first and has no "before" filter, so deciding
    what a purge will remove from its first page is reading the wrong end of the table:
    once the log is bigger than a page, every row in that page can be *newer* than the
    cutoff, and the command reports "nothing to do" about a deletion of thousands.
    Counting by subtraction — total minus what survives the cutoff — has no such blind
    spot. 610 rows because the page cap is 500, so a page-based count sees only new ones.
    """
    db = _fresh_db()
    try:
        _seed_audit(db, 600, age_days=90)
        _seed_audit(db, 10, age_days=0)
        assert store.audit.count_entries() == 610

        code, out = _run("audit", "--purge-before", "30d", "--yes", db=db)
        assert code == 0, out
        assert "about to delete 600 of 610 audit entries" in out, out
        assert "sampled from the newest 500 of them" in out, out
        assert "deleted 600 entries" in out, out

        _reopen(db)
        # 10 recent + the purge's own record.
        assert store.audit.count_entries() == 11
        assert "11 remain, including the record of this purge" in out, out
    finally:
        _restore_memory_db()


def test_the_purge_records_itself_and_the_record_survives():
    """A trimmed log must still say that it was trimmed.

    The purge's own entry is timestamped after the cutoff it just applied, so it cannot
    delete itself. Without it the log has a gap in the dates and nothing explaining it,
    which is the difference between a record with a retention period and a record somebody
    edited.
    """
    db = _fresh_db()
    try:
        _seed_audit(db, 4, age_days=60)
        _seed_audit(db, 2, age_days=1)

        code, out = _run("audit", "--purge-before", "30d", "--yes", db=db)
        assert code == 0, out

        entries, total = _entries(db, action=store.audit.AUDIT_PURGE)
        assert total == 1, out
        entry = entries[0]
        assert entry.detail["removed"] == 4
        assert entry.detail["spec"] == "30d"
        assert entry.detail["cutoff"] < entry.created_at, \
            "the purge record is older than the cutoff it applied — it would delete itself"
        # It names what went, not just how much: a count alone cannot be checked later.
        assert entry.detail["oldest_removed"] < entry.detail["cutoff"]

        # Running it again is a no-op that does not accumulate records of nothing.
        code, out = _run("audit", "--purge-before", "30d", "--yes", db=db)
        assert code == 0, out
        assert "nothing to do" in out, out
        _, total = _entries(db, action=store.audit.AUDIT_PURGE)
        assert total == 1, "a purge that deleted nothing recorded itself anyway"
    finally:
        _restore_memory_db()


def test_the_purge_will_not_assume_consent_without_a_tty():
    """No ``--yes`` and no terminal means refuse, not proceed.

    This is the command that deletes evidence, and the place it will actually be run from
    is a deployment script — where an interactive prompt is silently unanswerable. Refusing
    is the only safe reading of "nobody is here to confirm".
    """
    db = _fresh_db()
    try:
        _seed_audit(db, 5, age_days=60)
        code, out = _run("audit", "--purge-before", "30d", db=db)
        assert code == 1, "a purge went ahead with nobody to confirm it"
        _reopen(db)
        assert store.audit.count_entries() == 5, "rows were deleted anyway"
        # It says how to proceed, since the answer is almost always "pass --yes".
        assert "--yes" in _confirm_message(), _confirm_message()
    finally:
        _restore_memory_db()


def _confirm_message() -> str:
    """The refusal text, captured from :func:`manage._confirm` directly."""
    real_stdin, sys.stdin = sys.stdin, io.StringIO()
    try:
        manage._confirm("proceed?", assume_yes=False)
    except CommandError as exc:
        return str(exc)
    finally:
        sys.stdin = real_stdin
    raise AssertionError("_confirm did not refuse without a tty")


def test_a_purge_of_everything_is_still_refused_a_bad_cutoff():
    """A cutoff that cannot be read deletes nothing at all.

    Failing closed here is the whole point: the alternative is a raw string compared
    against every ``created_at`` in the table.
    """
    db = _fresh_db()
    try:
        _seed_audit(db, 6, age_days=400)
        for junk in ("0d", "last tuesday", "2026-13-01"):
            code, _ = _run("audit", "--purge-before", junk, "--yes", db=db)
            assert code == 1, junk
            _reopen(db)
            assert store.audit.count_entries() == 6, junk
    finally:
        _restore_memory_db()


# --------------------------------------------------------------------------- #
# Who the operator is
# --------------------------------------------------------------------------- #
def test_the_operator_is_recorded_as_a_shell_not_as_an_admin():
    """``actor_role`` is ``shell`` and ``actor_id`` is null.

    There is no account here — that is the premise of ``manage.py`` — so the role column
    records *how* the operator got in rather than what a role table granted them. Writing
    ``admin`` would be a lie that matters: reading the log a month later, "someone had a
    shell on the host" and "an account with the admin role did this" are different facts
    with different remedies, and only one of them can be fixed by changing a password.
    """
    db = _fresh_db()
    try:
        _seed_audit(db, 3, age_days=60)
        code, _ = _run("audit", "--purge-before", "30d", "--yes", db=db)
        assert code == 0

        entry = _entries(db, action=store.audit.AUDIT_PURGE)[0][0]
        assert entry.actor_id is None, "a shell command claimed an account id"
        assert entry.actor_role == "shell"
        assert entry.actor_email.endswith("@shell"), entry.actor_email
        assert "@" in entry.actor_email and entry.actor_email != "@shell"
        # And it says which door it came through, so the same identity written by two
        # different tools is still distinguishable.
        assert entry.detail["via"] == "manage.py"
    finally:
        _restore_memory_db()


def test_a_deletion_from_the_shell_is_audited_like_any_other():
    """Shell access is a stronger credential than a token, not an exemption from the log.

    The entry is written from the row read a moment before the DELETE, because afterwards
    there is nothing left to describe and a line reading only "deleted <uuid>" is not a
    record of what was lost.
    """
    db = _fresh_db()
    try:
        _reopen(db)
        user = store.create_user(email="cop@test.gov.in", password="password123",
                                 name="Officer", role="officer")
        row = store.save_scan(
            {"verdict": "non_compliant", "score": 41.0, "category": "packaged_food",
             "packs_applied": ["legal_metrology_2011"],
             "summary": {"checks_total": 9, "passed": 4, "failed": 5, "skipped": 0,
                         "violations_by_severity": {"critical": 3, "major": 2, "minor": 0}},
             "violations": []},
            user_id=user.id, product_name="Bhujia 200g", note="shelf 3",
        )

        code, out = _run("delete-scan", row.id[:8], "--yes", db=db)
        assert code == 0, out
        assert f"deleted {row.id}" in out, out

        _reopen(db)
        assert store.get_scan(row.id) is None, "the inspection is still there"
        entries, total = store.audit.list_entries(action=store.audit.SCAN_DELETE)
        assert total == 1, out
        entry = entries[0]
        assert entry.target == row.id
        assert entry.actor_role == "shell" and entry.actor_id is None
        assert entry.detail["owner_id"] == user.id
        assert entry.detail["verdict"] == "non_compliant"
        assert entry.detail["product_name"] == "Bhujia 200g"
        assert entry.detail["created_at"] == row.created_at
        assert entry.detail["via"] == "manage.py"
    finally:
        _restore_memory_db()


def test_a_cancelled_deletion_leaves_no_trace():
    """Refusing to confirm must delete nothing and record nothing.

    An audit log that records attempts as though they succeeded is worse than a shorter
    one, because then every entry has to be corroborated before it means anything.
    """
    db = _fresh_db()
    try:
        _reopen(db)
        row = store.save_scan({"verdict": "compliant", "score": 100.0,
                               "category": "packaged_food"}, product_name="Keep me")

        code, out = _run("delete-scan", row.id, db=db)      # no --yes, no tty
        assert code == 1, out
        _reopen(db)
        assert store.get_scan(row.id) is not None, "cancelled but deleted anyway"
        assert store.audit.count_entries() == 0, "a refusal was logged as a deletion"
    finally:
        _restore_memory_db()


# --------------------------------------------------------------------------- #
# Reading the log
# --------------------------------------------------------------------------- #
def test_the_log_is_read_newest_first():
    """Most recent at the top, because that is the question being asked.

    Somebody opening this is nearly always asking "what just happened", so oldest-first
    would put the answer at the bottom of a fifty-row page.
    """
    db = _fresh_db()
    try:
        _seed_audit(db, 1, action=store.audit.PACKS_RELOAD, age_days=10)
        _seed_audit(db, 1, action=store.audit.USER_ROLE, age_days=5)
        _seed_audit(db, 1, action=store.audit.CORPUS_LIST, age_days=0)

        code, out = _run("audit", db=db)
        assert code == 0, out
        for column in ("WHEN", "ACTOR", "ROLE", "ACTION", "TARGET", "DETAIL"):
            assert column in out, column
        newest, middle, oldest = (out.index(store.audit.CORPUS_LIST),
                                  out.index(store.audit.USER_ROLE),
                                  out.index(store.audit.PACKS_RELOAD))
        assert newest < middle < oldest, out
        assert "3 of 3 matching entries" in out, out
        # The role travelled with the row rather than being joined from `users` on read.
        assert "officer" in out, out
    finally:
        _restore_memory_db()


def test_an_empty_log_says_what_it_would_have_recorded():
    """"Nothing is recorded" and "nothing matched your filter" are different answers.

    An empty table with no explanation reads like a broken feature, so the unfiltered
    case spells out what counts as an event. With a filter it must stay quiet — there
    the emptiness is about the filter, and repeating the blurb would be misleading.
    """
    db = _fresh_db()
    try:
        code, out = _run("audit", db=db)
        assert code == 0, out
        assert "(none)" in out and "0 of 0 matching entries" in out, out
        assert "The log is empty" in out, out
        assert "not theirs" in out and "is not an event" in out, out

        code, out = _run("audit", "--action", store.audit.CORPUS_EXPORT, db=db)
        assert code == 0, out
        assert "0 of 0 matching entries" in out, out
        assert "The log is empty" not in out, \
            "a filter that matched nothing was reported as an empty log"
    finally:
        _restore_memory_db()

def test_every_filter_narrows_and_they_compose():
    """``--action`` / ``--target`` / ``--since``, alone and together.

    Read through ``--json`` rather than the table, because the table trims the target to
    eight characters and ``scan-0001`` and ``scan-0010`` are indistinguishable there.
    """
    db = _fresh_db()
    try:
        _seed_audit(db, 4, action=store.audit.CORPUS_EXPORT, age_days=10)
        _seed_audit(db, 3, action=store.audit.CORPUS_LIST, age_days=0)

        def ids(*argv):
            code, out = _run("audit", "--json", *argv, db=db)
            assert code == 0, out
            return [e["target"] for e in json.loads(out)]

        assert len(ids()) == 7
        assert len(ids("--action", store.audit.CORPUS_EXPORT)) == 4
        assert ids("--target", "scan-0002") == ["scan-0002", "scan-0002"]
        assert len(ids("--since", "5d")) == 3, "an older entry survived --since"
        # Composed: the same target, but only the recent one of its two entries.
        assert ids("--target", "scan-0002", "--since", "5d") == ["scan-0002"]
        assert ids("--action", store.audit.CORPUS_LIST, "--since", "5d") == \
            ["scan-0002", "scan-0001", "scan-0000"]
        assert ids("--action", store.audit.CORPUS_EXPORT, "--since", "5d") == []

        # The footer states the cutoff, so a short page is not mistaken for a short log.
        _, out = _run("audit", "--since", "5d", db=db)
        assert "3 of 3 matching entries since " in out, out
    finally:
        _restore_memory_db()


def test_the_actor_filter_takes_an_email_and_an_unknown_one_is_refused():
    """Filtering by actor resolves an account first, so a typo cannot read as "no results".

    ``--actor nobdy@x`` silently returning an empty page is the failure mode worth
    avoiding: it looks exactly like an innocent account.
    """
    db = _fresh_db()
    try:
        _reopen(db)
        user = store.create_user(email="admin@test.gov.in", password="password123",
                                 name="Admin", role="admin")
        store.audit.record(store.audit.USER_ROLE, actor_id=user.id,
                           actor_email=user.email, actor_role="admin",
                           target="someone-else", detail={"to": "officer"})
        _seed_audit(db, 2)      # actor_id NULL — must not match the filter

        code, out = _run("audit", "--actor", user.email, "--json", db=db)
        assert code == 0, out
        assert [e["target"] for e in json.loads(out)] == ["someone-else"]
        # An id works too, since that is what the other listings print.
        code, out = _run("audit", "--actor", user.id, "--json", db=db)
        assert code == 0 and len(json.loads(out)) == 1, out

        code, out = _run("audit", "--actor", "nobody@test.gov.in", db=db)
        assert code == 1, "an unknown actor was reported as zero results"
        assert "no account matches" in out or code == 1
    finally:
        _restore_memory_db()

def test_json_carries_the_detail_the_table_had_to_trim():
    """The table is a scannable index; ``--json`` is the record.

    A fixed-width column cannot hold an export's filters, so the table trims and says so
    with an ellipsis. Anything that trims is unusable as evidence, which is why the same
    command has a form that trims nothing — and why the trimmed one must be visibly
    trimmed rather than quietly short.
    """
    db = _fresh_db()
    try:
        _reopen(db)
        target = uuid.uuid4().hex
        detail = {"verdict": "non_compliant", "category": "packaged_food",
                  "search": "haldiram bhujia namkeen 200g", "max_rows": 5000}
        store.audit.record(store.audit.CORPUS_EXPORT, actor_email="officer@test.gov.in",
                           actor_role="officer", target=target, detail=detail)

        code, table = _run("audit", db=db)
        assert code == 0, table
        assert "…" in table, "a detail too wide for the column was not marked as trimmed"
        assert target[:8] in table and target not in table, "the table printed a full id"

        code, out = _run("audit", "--json", db=db)
        assert code == 0, out
        entry = json.loads(out)[0]
        assert entry["target"] == target
        assert entry["detail"] == detail, "--json lost or trimmed part of the detail"
        assert set(entry) == {"id", "created_at", "actor_id", "actor_email",
                              "actor_role", "action", "target", "detail"}
    finally:
        _restore_memory_db()


def test_paging_walks_the_log_without_repeating_or_skipping_an_entry():
    """``--limit``/``--offset`` partition the log rather than overlapping it.

    Every one of the twelve entries shares a timestamp, which is the case that breaks a
    naive ``ORDER BY created_at`` — without the id tie-break, pages can repeat a row and
    drop another, and a log that loses a row while being read is not one.
    """
    db = _fresh_db()
    try:
        _seed_audit(db, 12)
        seen: list[str] = []
        for offset in (0, 5, 10):
            code, out = _run("audit", "--json", "--limit", "5",
                             "--offset", str(offset), db=db)
            assert code == 0, out
            seen.extend(e["target"] for e in json.loads(out))
        assert len(seen) == 12, seen
        assert len(set(seen)) == 12, "a page repeated an entry"
        assert set(seen) == {f"scan-{n:04d}" for n in range(12)}
        assert seen == sorted(seen, reverse=True), "paging lost the newest-first order"

        _, out = _run("audit", "--limit", "5", db=db)
        assert "5 of 12 matching entries" in out, out
    finally:
        _restore_memory_db()

def test_the_summary_counts_by_action_and_admits_which_page_it_counted():
    """``--summary`` aggregates the page it fetched, and says so in as many words.

    This is the one output somebody would quote at a review, so the gap between "60
    entries exist" and "these 10 were counted" has to be on screen. Without that line a
    reader draws a conclusion about the corpus from the most recent handful of it.
    """
    db = _fresh_db()
    try:
        _seed_audit(db, 30, action=store.audit.CORPUS_EXPORT)
        _seed_audit(db, 30, action=store.audit.CORPUS_LIST)

        code, out = _run("audit", "--summary", "--limit", "10", db=db)
        assert code == 0, out
        assert "ACTION" in out and "ACTOR" in out and "COUNT" in out, out
        assert "10 of 60 entries" in out, out
        assert "summarised over the 10 fetched; raise --limit for more" in out, out
        assert "oldest shown" in out and "newest" in out, out
        counted = sum(int(tok) for line in out.splitlines()
                      if line.startswith(("scans.", "stats.", "officer"))
                      for tok in line.split()[-1:] if tok.isdigit())
        assert counted == 20, out    # 10 by action + the same 10 by actor

        # Over the whole log the two actions come out even, and the three seeded
        # inspectors split it three ways — the counts are of rows, not of pages.
        code, out = _run("audit", "--summary", "--limit", "500", db=db)
        assert code == 0, out
        assert "60 of 60 entries" in out, out
        assert f"{store.audit.CORPUS_EXPORT}  30" in out.replace("   ", "  "), out
    finally:
        _restore_memory_db()


def test_the_reader_never_writes_to_the_log_it_is_reading():
    """Reading is not a privileged action, and a reader that logged itself would grow.

    Over HTTP the admin audit read *is* recorded, because a token was used. Here the
    credential is a shell, every command already writes one entry when it changes
    something, and a self-recording reader would mean the table could never be read to
    the end — each page turn adding a row.
    """
    db = _fresh_db()
    try:
        _seed_audit(db, 5)
        for argv in (("audit",), ("audit", "--json"), ("audit", "--summary"),
                     ("audit", "--action", store.audit.CORPUS_EXPORT)):
            code, out = _run(*argv, db=db)
            assert code == 0, out
        _reopen(db)
        assert store.audit.count_entries() == 5, "reading the log appended to it"
        assert _all_actions(db) == [store.audit.CORPUS_EXPORT] * 5
    finally:
        _restore_memory_db()


def test_the_account_list_carries_each_accounts_inspection_count() -> None:
    """``users`` reports scans per account, and reports them from one query.

    This is the column that used to cost a query per row: the old loop asked
    ``list_scans(user_id=…, limit=1)`` for every account it printed, so a page of
    fifty accounts was fifty-one round trips. It now comes from the same LEFT JOIN
    that fills the admin screen, and this test exists because the rewrite is exactly
    the kind of change that can silently start reporting zero for everyone.
    """
    db = _fresh_db()
    try:
        _reopen(db)
        busy = store.create_user(email="busy@test.gov.in", password="password123",
                                 name="Busy Officer", role="officer")
        store.create_user(email="idle@test.gov.in", password="password123",
                          name="Idle Consumer", role="consumer")
        for _ in range(3):
            store.save_scan({"verdict": "compliant", "score": 100.0,
                             "category": "packaged_food", "packs_applied": [],
                             "summary": {}, "violations": []}, user_id=busy.id)

        code, out = _run("users", db=db)
        assert code == 0, out
        assert "2 of 2 account(s)" in out, out

        # The count belongs to the account that owns the scans, not to whoever
        # happens to be printed first.
        busy_line = next(ln for ln in out.splitlines() if "busy@" in ln)
        idle_line = next(ln for ln in out.splitlines() if "idle@" in ln)
        assert busy_line.split()[-3] == "3", busy_line
        assert idle_line.split()[-3] == "0", idle_line

        # An account with no scans is still listed. A JOIN that dropped it would be
        # the obvious way to get this wrong.
        assert "Idle Consumer" in out, out
    finally:
        _restore_memory_db()


def test_reading_the_account_list_does_not_change_it() -> None:
    """``users`` is a read: no audit row, no mutation, and paging is honest.

    The four accounts here are created inside one second, so they share a
    ``created_at`` — which is the case that used to make paging lie. Ordering by
    timestamp alone left the tie to SQLite, so page two could repeat an account page
    one had already shown and skip another entirely. The ordering now falls back to
    ``id``, and this test pins the property rather than a particular order: whatever
    sequence the two pages come back in, together they must be every account, once.
    """
    db = _fresh_db()
    try:
        _reopen(db)
        for i in range(4):
            store.create_user(email=f"u{i}@test.gov.in", password="password123",
                              name=f"User {i}", role="consumer")

        def emails(*argv: str) -> list[str]:
            code, out = _run("users", *argv, db=db)
            assert code == 0, out
            # "2 of 4" — the total is the register, not the page.
            assert "2 of 4 account(s)" in out, out
            return [w for ln in out.splitlines() for w in ln.split()
                    if w.endswith("@test.gov.in")]

        first = emails("--limit", "2")
        second = emails("--limit", "2", "--offset", "2")
        assert len(first) == 2 and len(second) == 2, (first, second)
        assert not set(first) & set(second), (first, second)
        assert set(first) | set(second) == {f"u{i}@test.gov.in" for i in range(4)}

        _reopen(db)
        assert store.count_users() == 4
        assert store.audit.count_entries() == 0, "reading the list wrote to the log"
    finally:
        _restore_memory_db()


if __name__ == "__main__":
    raise SystemExit(apiclient.run_all(globals(), title="manage.py tests"))




