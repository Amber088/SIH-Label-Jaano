#!/usr/bin/env python3
"""
manage.py — local administration for the Label Jaano history database.

    python manage.py init                      create/upgrade the database
    python manage.py createuser --role officer  enrol a member of staff
    python manage.py users                     who has an account
    python manage.py role a@b.in officer       promote or demote
    python manage.py disable a@b.in            revoke access immediately
    python manage.py passwd a@b.in             reset a password
    python manage.py scans                     recent inspections
    python manage.py report <scan_id> -o r.html   export a print-ready report
    python manage.py stats                     corpus aggregates
    python manage.py audit --since 7d          who exercised a privilege, and over what
    python manage.py secret                    generate LABEL_JAANO_SECRET
    python manage.py seed --yes                populate a demo corpus

Why a CLI at all, when there is a perfectly good HTTP API?

Because three operations must not be reachable over HTTP, and one is easier without it.

*Creating an administrator* is the first. An admin can read every inspection in the
corpus, delete any of them, and swap out the rule packs that every verdict is measured
against. If an HTTP endpoint could mint one, the security of the whole deployment would
rest on that endpoint's own authentication — a bootstrapping circle. Requiring shell
access on the host instead makes the trust boundary something physical.

*Resetting somebody else's password* is the second, for the same reason.

*Trimming the audit log* is the third. Over HTTP the log is readable by an admin and
has no DELETE route at all, deliberately: a record that the API it audits can erase is
not evidence. Retention is still a real requirement, so it is enacted here — where the
operator has already proved more than a token could — and the purge writes its own
entry, which survives because its timestamp is necessarily after the cutoff it applied.

And *exporting a report* is the last, for a duller reason: during an inspection the
useful artifact is a file on disk you can attach to an email, and asking an officer to
authenticate a browser session to obtain it is friction with no security benefit — the
shell already proved more than a token ever could.

This module imports ``store``, ``auth``, ``rule_engine`` and ``reports``, and
deliberately never imports FastAPI or pydantic, so it runs on a server where the web
dependencies were never installed. Standard library only.
"""
from __future__ import annotations

import argparse
import getpass
import json
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import auth  # noqa: E402
import store  # noqa: E402
from auth.roles import Role  # noqa: E402

EXIT_OK = 0
EXIT_ERROR = 1


class CommandError(Exception):
    """A failure worth a clean one-line message rather than a traceback."""


# --------------------------------------------------------------------------- #
# Presentation helpers
# --------------------------------------------------------------------------- #
def _table(headers: list[str], rows: list[list[str]]) -> str:
    """Fixed-width table. Wide enough for the data, no wider."""
    if not rows:
        return "(none)"
    cells = [[("" if c is None else str(c)) for c in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in cells:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)).rstrip()
    rule = "  ".join("-" * w for w in widths)
    body = [
        "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip()
        for row in cells
    ]
    return "\n".join([line, rule, *body])


def _fmt_bytes(n: int | None) -> str:
    if not n:
        return "-"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return "-"


def _short(text: str | None, width: int = 28) -> str:
    text = (text or "").strip()
    if len(text) <= width:
        return text or "-"
    return text[: width - 1] + "…"


# --------------------------------------------------------------------------- #
# Input helpers
# --------------------------------------------------------------------------- #
def _resolve_role(value: str) -> Role:
    """Strict role parse — the CLI must not use :meth:`Role.parse`.

    ``Role.parse`` is deliberately lenient: anything it does not recognise becomes
    ``consumer``, so a corrupted database column can never accidentally grant access.
    That is exactly the wrong behaviour at a command prompt. ``manage.py role
    a@b.in offcier`` would silently *demote* the person you were trying to promote,
    report success, and leave you to discover it when they could not open the queue.
    """
    text = (value or "").strip().lower()
    for role in Role:
        if role.value == text:
            return role
    raise CommandError(
        f"unknown role {value!r}. Choose one of: "
        + ", ".join(f"{r.value} ({r.label})" for r in Role)
    )


def _read_password(args, *, confirm: bool, prompt: str = "Password: ") -> str:
    """Obtain a password without writing it into the shell history.

    ``--password`` exists for scripts but is the worst option: the value lands in
    the shell history and is visible in ``ps`` output to every user on the box while
    the command runs. ``--password-stdin`` is the option to reach for when
    automating; an interactive prompt is the default because it leaks neither.
    """
    if getattr(args, "password", None):
        return args.password
    if getattr(args, "password_stdin", False):
        data = sys.stdin.readline()
        if not data.strip():
            raise CommandError("--password-stdin was given but stdin was empty")
        return data.rstrip("\n")
    if not sys.stdin.isatty():
        raise CommandError(
            "no terminal available to prompt for a password; pass --password-stdin "
            "and pipe it in, e.g. `echo 's3cret' | python manage.py createuser ... "
            "--password-stdin`"
        )
    first = getpass.getpass(prompt)
    if confirm and first != getpass.getpass("Confirm: "):
        raise CommandError("the two passwords did not match")
    return first


def _validate_password(password: str) -> None:
    problem = auth.password_problem(password)
    if problem:
        raise CommandError(problem)


def _find_user(identifier: str):
    """Look a user up by email or by id, so either can be pasted from a listing."""
    user = store.get_user_by_email(identifier) or store.get_user(identifier.strip())
    if user is None:
        raise CommandError(f"no account matches {identifier!r} (tried email, then id)")
    return user


def _confirm(question: str, *, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        raise CommandError(f"{question} — refusing to assume; pass --yes")
    return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")


def _find_scan(identifier: str):
    """Resolve a full *or abbreviated* inspection id.

    Abbreviations have to work: the ``scans`` listing prints eight characters, and
    that is what anyone will copy. A command that only accepted the full 32 would be
    asking people to go and look it up in SQLite first.
    """
    ident = (identifier or "").strip()
    if not ident:
        raise CommandError("an inspection id is required")
    row = store.get_scan(ident)
    if row is not None:
        return row
    matches = [r for r in store.list_scans(limit=200)[0] if r.id.startswith(ident)]
    if len(matches) == 1:
        return store.get_scan(matches[0].id)
    if len(matches) > 1:
        raise CommandError(
            f"{ident!r} matches {len(matches)} inspections "
            f"({', '.join(r.id[:12] for r in matches[:4])}…); use more characters"
        )
    raise CommandError(f"no inspection with id {ident!r}")


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_init(args) -> int:
    version = store.init_schema(store.connection())
    info = store.db_stats()
    print(f"database ready at {info['path']}")
    print(f"schema version {version}  ·  {info['users']} users  ·  {info['scans']} scans")
    if auth.secret_is_ephemeral():
        print(
            "\nnote: LABEL_JAANO_SECRET is not set, so tokens are signed with a key\n"
            "      generated per process — every restart signs everyone out. Run\n"
            "      `python manage.py secret` to generate one worth keeping."
        )
    return EXIT_OK


def cmd_createuser(args) -> int:
    # Validate everything that cannot depend on the database *before* opening it, so
    # a typo'd role or address does not leave a freshly-created empty DB file behind.
    role = _resolve_role(args.role)
    email = store.normalise_email(args.email)
    if not email or "@" not in email:
        raise CommandError(f"{args.email!r} is not a usable email address")

    password = _read_password(args, confirm=True, prompt=f"Password for {email}: ")
    _validate_password(password)

    store.init_schema(store.connection())
    try:
        user = store.create_user(email, password, name=args.name or "", role=role)
    except store.UserExists:
        raise CommandError(
            f"{email} is already registered. Use `manage.py role {email} {role.value}` "
            f"to change their role, or `manage.py passwd {email}` to reset the password."
        )
    print(f"created {user.email}  ·  {user.role.label}  ·  id {user.id}")
    if role is Role.ADMIN:
        print(
            "\nThis account can read and delete every inspection in the corpus and\n"
            "replace the rule packs that all verdicts are measured against."
        )
    return EXIT_OK


def cmd_users(args) -> int:
    # One query for the page and one for the total, not one per account: the old
    # loop ran a count query per row, which is invisible on a seeded demo and
    # painful on a real district's register.
    rows_in, total = store.list_users_page(limit=args.limit, offset=args.offset)
    rows = []
    for u, scans in rows_in:
        rows.append([
            u.email,
            _short(u.name, 20),
            u.role.value,
            "disabled" if u.disabled else "active",
            str(scans),
            u.created_at[:10],
            u.id[:8],
        ])
    print(_table(
        ["EMAIL", "NAME", "ROLE", "STATUS", "SCANS", "JOINED", "ID"], rows
    ))
    shown = len(rows_in)
    print(f"\n{shown} of {total} account(s)")
    return EXIT_OK


def cmd_role(args) -> int:
    user = _find_user(args.email)
    role = _resolve_role(args.role)
    if user.role is role:
        print(f"{user.email} is already {role.label.lower()}; nothing to do")
        return EXIT_OK
    was = user.role
    if not store.set_role(user.id, role):
        raise CommandError(f"could not update {user.email}")
    print(f"{user.email}: {was.label} -> {role.label}")
    if role is Role.ADMIN:
        print("this account can now reload rule packs and delete any inspection")
    return EXIT_OK


def cmd_disable(args) -> int:
    user = _find_user(args.email)
    disable = not args.enable
    if user.disabled == disable:
        print(f"{user.email} is already {'disabled' if disable else 'active'}")
        return EXIT_OK
    if not store.set_disabled(user.id, disable):
        raise CommandError(f"could not update {user.email}")
    if disable:
        # Worth stating plainly: the API re-reads the row on every request, so this
        # takes hold at once rather than when an already-issued token expires.
        print(f"{user.email} disabled — any token they hold stops working immediately")
    else:
        print(f"{user.email} re-enabled (their old tokens may have expired meanwhile)")
    return EXIT_OK


def cmd_passwd(args) -> int:
    user = _find_user(args.email)
    password = _read_password(args, confirm=True, prompt=f"New password for {user.email}: ")
    _validate_password(password)
    verifier = auth.hash_password(password)
    changed = store.db.execute(
        "UPDATE users SET password_hash = :h WHERE id = :id",
        {"h": verifier, "id": user.id},
    )
    if not changed:
        raise CommandError(f"could not update {user.email}")
    print(f"password reset for {user.email}")
    print(
        "note: tokens already issued to this account remain valid until they expire.\n"
        "      If the concern is a compromised session rather than a forgotten\n"
        f"      password, also run `manage.py disable {user.email}`."
    )
    return EXIT_OK


def cmd_scans(args) -> int:
    user_id = None
    if args.user:
        user_id = _find_user(args.user).id
    rows, total = store.list_scans(
        user_id=user_id,
        verdict=args.verdict,
        category=args.category,
        search=args.search,
        limit=args.limit,
        offset=args.offset,
    )
    emails: dict[str, str] = {}

    def owner(uid: str | None) -> str:
        if not uid:
            return "(anonymous)"
        if uid not in emails:
            u = store.get_user(uid)
            emails[uid] = u.email if u else uid[:8]
        return emails[uid]

    table = [
        [
            r.id[:8],
            r.created_at[:16].replace("T", " "),
            r.verdict,
            f"{r.score:.0f}",
            r.category,
            _short(r.product_name, 24),
            owner(r.user_id),
            "mock" if r.mock else r.source,
        ]
        for r in rows
    ]
    print(_table(
        ["ID", "WHEN", "VERDICT", "SCORE", "CATEGORY", "PRODUCT", "BY", "SOURCE"], table
    ))
    print(f"\n{len(rows)} of {total} matching inspection(s)")
    return EXIT_OK


def cmd_report(args) -> int:
    from reports import render_inspection_report

    row = _find_scan(args.scan_id)
    if not row.report:
        raise CommandError(
            f"inspection {row.id} has no stored report body, so there is nothing to render"
        )

    inspector = None
    if row.user_id:
        user = store.get_user(row.user_id)
        inspector = user.to_dict() if user else None

    html = render_inspection_report(
        row.report,
        scan_id=row.id,
        created_at=row.created_at,
        product_name=row.product_name,
        note=row.note,
        location=row.location,
        source=row.source,
        mock=row.mock,
        inspector=inspector,
        include_appendix=not args.no_appendix,
    )

    if args.output in (None, "-"):
        sys.stdout.write(html)
        return EXIT_OK
    out = Path(args.output).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out}  ({len(html):,} bytes)")
    print("open it in a browser and use Print -> Save as PDF for a filed copy")
    return EXIT_OK


def cmd_stats(args) -> int:
    user_id = _find_user(args.user).id if args.user else None
    info = store.db_stats()
    data = store.aggregate_stats(user_id=user_id)

    print(f"database   {info['path']}")
    print(f"           schema v{info['schema_version']}  ·  {_fmt_bytes(info['size_bytes'])}")
    print(f"accounts   {info['users']}")
    scope = f"for {args.user}" if args.user else "across every account"
    print(f"\ninspections {scope}")
    print(f"  total          {data['total_scans']}")
    print(f"  scored         {data['scored_scans']}  (label-detected reads only)")
    print(f"  average score  {data['average_score']:.1f}")
    print(f"  compliant      {data['compliance_rate']:.1f}%")

    by_verdict = data.get("by_verdict") or {}
    if by_verdict:
        print("\nby verdict")
        for verdict, n in sorted(by_verdict.items(), key=lambda kv: -kv[1]):
            print(f"  {verdict:<20} {n}")

    sev = data.get("violations_by_severity") or {}
    if data.get("violations_total"):
        parts = "  ".join(f"{k} {sev.get(k, 0)}" for k in ("critical", "major", "minor"))
        print(f"\ncontraventions   {data['violations_total']}   ({parts})")

    cats = data.get("by_category") or []
    if cats:
        print("\nby category")
        print(_table(
            ["CATEGORY", "SCANS", "AVG SCORE"],
            [[c["category"], c["scans"], f"{c['average_score']:.1f}"] for c in cats],
        ))

    top = data.get("top_violations") or []
    if top:
        print("\nmost breached declarations")
        print(_table(
            ["DECLARATION", "RULE", "SEVERITY", "FAILS", "PRODUCTS"],
            [
                [_short(v["declaration_label"], 34), _short(v["legal_reference"], 30),
                 v["severity"], v["occurrences"], v["scans_affected"]]
                for v in top
            ],
        ))
    return EXIT_OK


def cmd_delete_scan(args) -> int:
    row = _find_scan(args.scan_id)
    label = row.product_name or row.category
    if not _confirm(
        f"permanently delete inspection {row.id[:8]} ({label}, {row.verdict})?",
        assume_yes=args.yes,
    ):
        print("cancelled")
        return EXIT_OK
    store.delete_scan(row.id)
    # Recorded for the same reason the HTTP delete is: shell access is a stronger
    # credential than any token, not an exemption from the log. Described from the row
    # read a moment ago, because after the DELETE there is nothing left to describe.
    store.audit.record(
        store.audit.SCAN_DELETE,
        target=row.id,
        detail={
            "owner_id": row.user_id,
            "verdict": row.verdict,
            "product_name": row.product_name,
            "created_at": row.created_at,
            **_shell_actor_detail(),
        },
        **_shell_actor(),
    )
    print(f"deleted {row.id}")
    return EXIT_OK


# --------------------------------------------------------------------------- #
# Audit trail
# --------------------------------------------------------------------------- #
def _shell_actor() -> dict:
    """Identify the operator for an audit entry written from the command line.

    There is no account here — that is the whole premise of this file — so
    ``actor_id`` stays null and the role column records *how* the operator got in
    rather than what a role table granted them. Writing ``admin`` would be a lie that
    matters: reading the log later, "this was done by someone with a shell on the
    host" and "this was done by an account with the admin role" are different facts
    with different remedies.
    """
    try:
        who = getpass.getuser()
    except Exception:  # noqa: BLE001 - no passwd entry (container); not worth failing
        who = "unknown"
    return {"actor_id": None, "actor_email": f"{who}@shell", "actor_role": "shell"}


def _shell_actor_detail() -> dict:
    return {"via": "manage.py"}


def _parse_when(value: str) -> str:
    """A cutoff timestamp from either an age (``30d``) or an absolute date.

    Accepts ``90m`` / ``24h`` / ``30d``, a bare ``YYYY-MM-DD``, or a full ISO
    timestamp. What comes back is compared lexicographically against ``created_at``,
    which is sound because every stored timestamp is UTC ISO-8601 with fixed width —
    the string order *is* the chronological order.

    That fixed width is also why a date is re-formatted rather than passed through. An
    unpadded ``2026-7-1`` sorts *after* every real timestamp of that year, so used
    verbatim it would match nothing as a ``--since`` and everything as a
    ``--purge-before``. Round-tripping it through :meth:`strptime` normalises it to
    ``2026-07-01``, and anything that is not a date at all fails here with a message
    rather than silently comparing as a string.
    """
    text = (value or "").strip()
    if not text:
        raise CommandError("a date or age is required")

    unit_seconds = {"m": 60, "h": 3600, "d": 86400}
    if len(text) > 1 and text[-1].lower() in unit_seconds and text[:-1].isdigit():
        amount = int(text[:-1])
        if amount <= 0:
            raise CommandError(f"{value!r}: the age must be a positive number")
        moment = datetime.now(timezone.utc) - timedelta(
            seconds=amount * unit_seconds[text[-1].lower()]
        )
        return moment.isoformat(timespec="seconds").replace("+00:00", "Z")

    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(text.rstrip("Z"), fmt)
        except ValueError:
            continue
        if fmt == "%Y-%m-%d":
            # Midnight UTC on that date; the prefix compare includes the whole day.
            return parsed.strftime("%Y-%m-%d")
        return parsed.strftime("%Y-%m-%dT%H:%M:%SZ")

    raise CommandError(
        f"cannot read {value!r} as a time. Use an age (30d, 24h, 90m), a date "
        f"(2026-08-01) or a timestamp (2026-08-01T09:30:00Z)."
    )


def _detail_summary(detail: dict | None, width: int) -> str:
    """``k=v`` pairs, shortest-first, trimmed to *width*. Full text is in --json."""
    if not detail:
        return "-"
    parts = []
    for key, value in detail.items():
        if value is None or value == "" or value == []:
            continue
        if isinstance(value, (list, tuple)):
            value = ",".join(str(v) for v in value)
        elif isinstance(value, dict):
            value = json.dumps(value, separators=(",", ":"), default=str)
        parts.append(f"{key}={value}")
    return _short(" ".join(parts), width) if parts else "-"


def cmd_audit(args) -> int:
    """Read the privileged-action log, or enact a retention period.

    The log answers one question — who exercised a privilege over what, and when —
    and it is only useful if it can be read where the operator already is. Over HTTP
    it is admin-only and deliberately has no DELETE route, so this is the only place
    an entry can be removed at all, and the removal is itself recorded.
    """
    if args.purge_before:
        return _audit_purge(args)

    actor_id = None
    if args.actor:
        actor_id = _find_user(args.actor).id
    since = _parse_when(args.since) if args.since else None

    entries, total = store.audit.list_entries(
        actor_id=actor_id, action=args.action, target=args.target,
        since=since, limit=args.limit, offset=args.offset,
    )

    if args.json:
        print(json.dumps([e.to_dict() for e in entries], indent=2))
        return EXIT_OK

    if args.summary:
        return _audit_summary(entries, total, since)

    print(_table(
        ["WHEN", "ACTOR", "ROLE", "ACTION", "TARGET", "DETAIL"],
        [
            [
                e.created_at[:16].replace("T", " "),
                _short(e.actor_email or "(none)", 26),
                e.actor_role or "-",
                e.action,
                (e.target or "-")[:8],
                _detail_summary(e.detail, 44),
            ]
            for e in entries
        ],
    ))
    print(f"\n{len(entries)} of {total} matching entr{'y' if total == 1 else 'ies'}"
          + (f" since {since}" if since else ""))
    if not entries and not (actor_id or args.action or args.target or since):
        print(
            "\nThe log is empty. It records privileged actions only — an officer\n"
            "opening the whole-corpus queue or export, someone reading an inspection\n"
            "that is not theirs, a deletion, an account change, a rule-pack reload.\n"
            "A consumer scanning a label and reading their own history is not an event."
        )
    return EXIT_OK


def _audit_summary(entries, total: int, since: str | None) -> int:
    """Counts by action and by actor over the page that was fetched."""
    by_action: dict[str, int] = {}
    by_actor: dict[str, int] = {}
    for e in entries:
        by_action[e.action] = by_action.get(e.action, 0) + 1
        by_actor[e.actor_email or "(none)"] = by_actor.get(e.actor_email or "(none)", 0) + 1
    print(_table(
        ["ACTION", "COUNT"],
        [[a, str(n)] for a, n in sorted(by_action.items(), key=lambda kv: (-kv[1], kv[0]))],
    ))
    print()
    print(_table(
        ["ACTOR", "COUNT"],
        [[a, str(n)] for a, n in sorted(by_actor.items(), key=lambda kv: (-kv[1], kv[0]))],
    ))
    span = f" since {since}" if since else ""
    # Named explicitly: a summary over a page is a summary of the page. Raising --limit
    # is the fix, and saying so is cheaper than someone drawing a conclusion about the
    # corpus from the most recent hundred lines of it.
    print(f"\n{len(entries)} of {total} entr{'y' if total == 1 else 'ies'}{span} "
          f"(summarised over the {len(entries)} fetched; raise --limit for more)")
    if entries:
        print(f"oldest shown {entries[-1].created_at}  ·  newest {entries[0].created_at}")
    return EXIT_OK


def _audit_purge(args) -> int:
    cutoff = _parse_when(args.purge_before)

    # Counted by subtraction rather than by fetching the doomed rows. ``list_entries``
    # orders newest-first and has no "before" filter, so a page of it is the wrong end
    # of the table: on a large log every row in the first page can be *newer* than the
    # cutoff, and deciding from that page would report "nothing to do" about a purge
    # that was about to delete thousands of entries.
    total = store.audit.count_entries()
    _, surviving = store.audit.list_entries(since=cutoff, limit=1)
    going = total - surviving
    if going <= 0:
        print(f"no audit entries are older than {cutoff}; "
              f"nothing to do ({total} entr(y/ies) in the log)")
        return EXIT_OK

    # The oldest row is the last one in newest-first order, so its offset is the total
    # minus one. The doomed range begins at offset `surviving`, which is where a sample
    # of what is about to go can be read from.
    tail, _ = store.audit.list_entries(limit=1, offset=total - 1)
    oldest = tail[0].created_at if tail else "?"
    sample, _ = store.audit.list_entries(limit=min(going, 500), offset=surviving)
    actions = sorted({e.action for e in sample})

    print(f"about to delete {going} of {total} audit entr{'y' if going == 1 else 'ies'}, "
          f"from {oldest} up to {cutoff}")
    print("  actions affected: " + ", ".join(actions)
          + (" (sampled from the newest 500 of them)" if going > len(sample) else ""))
    if not _confirm(
        "this is the evidence log; deletions cannot be undone — proceed?",
        assume_yes=args.yes,
    ):
        print("cancelled")
        return EXIT_OK

    removed = store.audit.purge_before(cutoff)
    # The purge records itself, and its own entry survives because its timestamp is
    # necessarily after the cutoff it just applied. So the log can be trimmed to a
    # retention period without ever losing the fact that it was trimmed — a gap in the
    # dates with nothing explaining it is what makes a log stop being evidence.
    store.audit.record(
        store.audit.AUDIT_PURGE,
        detail={"cutoff": cutoff, "removed": removed, "spec": args.purge_before,
                "oldest_removed": oldest, **_shell_actor_detail()},
        **_shell_actor(),
    )
    print(f"deleted {removed} entr{'y' if removed == 1 else 'ies'} older than {cutoff}")
    print(f"{store.audit.count_entries()} remain, including the record of this purge")
    return EXIT_OK


def cmd_secret(args) -> int:
    value = secrets.token_urlsafe(48)
    print(value)
    print(
        "\nExport this before starting the server so sessions survive a restart:\n"
        f"  export LABEL_JAANO_SECRET='{value}'\n"
        "\nChanging it later signs every user out, which is also how you revoke all\n"
        "outstanding tokens at once — there is no per-token deny list.",
        file=sys.stderr,
    )
    return EXIT_OK


# --------------------------------------------------------------------------- #
# Demo corpus
# --------------------------------------------------------------------------- #
# Enough inspections, across enough categories and verdicts, that the dashboard and
# the officer queue have something to show. Each entry names a sample to start from
# and fields to drop, which is how the verdicts end up varied: removing a mandatory
# declaration is precisely what the rules are looking for.
_SEED_SCANS = [
    ("good_label.json", "Parle-G Glucose Biscuits 76g", "packaged_food", []),
    ("good_label.json", "Tata Salt Iodised 1kg", "packaged_food", []),
    ("bad_label.json", "Unbranded Toffee Jar", "packaged_food", []),
    ("good_label.json", "Amul Taaza Toned Milk 500ml", "packaged_food", ["mrp"]),
    ("good_label.json", "Aashirvaad Atta 5kg", "packaged_food", ["net_quantity"]),
    ("good_label.json", "Bisleri Mineral Water 1L", "packaged_water", ["fssai_license"]),
    ("bad_label.json", "Loose Spice Mix Packet", "packaged_food", ["manufacturer_details"]),
    ("good_label.json", "Nivea Soft Cream 100ml", "cosmetics", []),
    ("good_label.json", "Surf Excel Detergent 1kg", "household", ["consumer_care"]),
    ("good_label.json", "Dairy Milk Chocolate 50g", "packaged_food",
     ["ingredients_list", "nutritional_info"]),
]

# Declarations and marks that only a food label carries. The samples are food labels,
# so a cosmetics or detergent entry built from one would arrive holding an FSSAI
# licence number and a nutrition panel. Nobody would be fooled by that, and a judge
# who opens the record would rightly stop trusting the rest of the corpus — so the
# non-food entries are stripped to what such a pack would really declare. It also
# demonstrates the point of pack stacking: only the Legal Metrology base pack applies
# to these, and the FSSAI checks correctly never fire.
_FOOD_ONLY_FIELDS = (
    "fssai_license", "ingredients_list", "nutritional_info", "date_marking",
    "veg_nonveg_mark", "allergen_declaration", "additive_declaration",
)
_FOOD_ONLY_SYMBOLS = ("fssai_logo", "veg_nonveg_mark", "non_veg_mark", "veg_mark")
_FOOD_CATEGORIES = {"packaged_food", "food", "beverage", "packaged_water"}

_SEED_ACCOUNTS = [
    ("officer@demo.gov.in", "Officer Demo", Role.OFFICER),
    ("consumer@demo.in", "Consumer Demo", Role.CONSUMER),
]


def _make_plausible(raw: dict, category: str) -> dict:
    """Strip food-only declarations from a non-food seed entry (see above)."""
    if category in _FOOD_CATEGORIES:
        return raw
    fields = raw.get("fields") or {}
    for key in _FOOD_ONLY_FIELDS:
        fields.pop(key, None)
    raw["symbols_detected"] = [
        s for s in (raw.get("symbols_detected") or []) if s not in _FOOD_ONLY_SYMBOLS
    ]
    # The raw OCR text is what the FSSAI regex checks read, so leaving the food
    # sentences in it would re-introduce exactly what we just removed.
    raw["raw_text"] = " ".join(
        line for line in (raw.get("raw_text") or "").split(". ")
        if not any(
            token in line.lower()
            for token in ("fssai", "ingredient", "energy", "protein", "use by",
                          "best before", "veg")
        )
    )
    return raw


def cmd_seed(args) -> int:
    """Populate a demo corpus. Refuses to run against a database with real content."""
    from rule_engine import ScanInput, evaluate_scan

    store.init_schema(store.connection())
    info = store.db_stats()
    if info["scans"] and not args.force:
        raise CommandError(
            f"{info['path']} already holds {info['scans']} inspection(s). Seeding would "
            "mix demo data into a real corpus, and once mixed the two are hard to tell "
            "apart. Pass --force if this really is a scratch database."
        )
    if not _confirm(
        f"seed demo accounts and {len(_SEED_SCANS)} inspections into {info['path']}?",
        assume_yes=args.yes,
    ):
        print("cancelled")
        return EXIT_OK

    password = args.password or "demo-pass-123"
    _validate_password(password)

    created = []
    for email, name, role in _SEED_ACCOUNTS:
        existing = store.get_user_by_email(email)
        if existing:
            created.append((existing, "existing"))
            continue
        created.append((store.create_user(email, password, name=name, role=role), "new"))
    officer = created[0][0]
    consumer = created[1][0]

    samples = {}
    for filename in {s[0] for s in _SEED_SCANS}:
        path = _BACKEND_DIR / "samples" / filename
        if not path.exists():
            raise CommandError(f"missing sample fixture {path}")
        samples[filename] = json.loads(path.read_text(encoding="utf-8"))

    verdicts: dict[str, int] = {}
    for i, (filename, product, category, drop) in enumerate(_SEED_SCANS):
        raw = json.loads(json.dumps(samples[filename]))  # deep copy per scan
        raw["category"] = category
        for key in drop:
            (raw.get("fields") or {}).pop(key, None)
        raw = _make_plausible(raw, category)
        report = evaluate_scan(ScanInput.from_dict(raw))
        payload = report.to_dict()
        # Alternate the owner so "my history" and "the whole corpus" differ, which is
        # the only way a demo can show that role scoping does anything at all.
        owner = officer if i % 3 else consumer
        store.save_scan(
            payload,
            user_id=owner.id,
            source="json",
            mock=False,
            product_name=product,
            note="Seeded demo inspection",
            location="Pune, Maharashtra",
            scan_input=raw,
        )
        verdicts[payload["verdict"]] = verdicts.get(payload["verdict"], 0) + 1

    print(_table(
        ["EMAIL", "ROLE", "STATE"],
        [[u.email, u.role.value, state] for u, state in created],
    ))
    print(f"\npassword for both demo accounts: {password}")
    print(f"\nseeded {len(_SEED_SCANS)} inspections: "
          + ", ".join(f"{n} {v}" for v, n in sorted(verdicts.items())))
    print("run `python manage.py stats` to see the dashboard aggregates")
    return EXIT_OK


# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="manage.py",
        description="Administer the Label Jaano history database (stdlib only).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Environment\n"
            "  LABEL_JAANO_DB           path to the SQLite file (default data/labeljaano.db)\n"
            "  LABEL_JAANO_SECRET       token signing key; unset means per-process\n"
            "  LABEL_JAANO_OFFICER_CODE shared code that lets sign-up choose 'officer'\n"
        ),
    )
    parser.add_argument(
        "--db", metavar="PATH",
        help="database file to operate on (overrides LABEL_JAANO_DB for this command)",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    def add(name: str, fn, help_text: str, **kw):
        p = sub.add_parser(name, help=help_text, description=fn.__doc__ or help_text, **kw)
        p.set_defaults(func=fn)
        return p

    def add_password_args(p):
        p.add_argument("--password", help="INSECURE: visible in shell history and ps")
        p.add_argument("--password-stdin", action="store_true",
                       help="read the password from the first line of stdin")

    add("init", cmd_init, "create or upgrade the database")

    p = add("createuser", cmd_createuser, "create an account (the only way to make an admin)")
    p.add_argument("--email", required=True)
    p.add_argument("--name", default="")
    p.add_argument("--role", default="consumer",
                   help="consumer | officer | admin (no enrolment code needed here)")
    add_password_args(p)

    p = add("users", cmd_users, "list accounts")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--offset", type=int, default=0)

    p = add("role", cmd_role, "change an account's role")
    p.add_argument("email", help="email address or account id")
    p.add_argument("role", help="consumer | officer | admin")

    p = add("disable", cmd_disable, "disable an account (revokes its tokens at once)")
    p.add_argument("email", help="email address or account id")
    p.add_argument("--enable", action="store_true", help="re-enable instead")

    p = add("passwd", cmd_passwd, "reset an account's password")
    p.add_argument("email", help="email address or account id")
    add_password_args(p)

    p = add("scans", cmd_scans, "list stored inspections")
    p.add_argument("--user", help="only this account's inspections (email or id)")
    p.add_argument("--verdict", help="compliant | needs_review | non_compliant | no_label_detected")
    p.add_argument("--category")
    p.add_argument("--search", help="substring of product name, note or location")
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--offset", type=int, default=0)

    p = add("report", cmd_report, "export one inspection as a print-ready HTML report")
    p.add_argument("scan_id", help="full or abbreviated inspection id")
    p.add_argument("-o", "--output", help="file to write ('-' for stdout)")
    p.add_argument("--no-appendix", action="store_true",
                   help="omit the check-by-check audit appendix")

    p = add("stats", cmd_stats, "corpus aggregates")
    p.add_argument("--user", help="scope to one account (email or id)")

    p = add("delete-scan", cmd_delete_scan, "delete one stored inspection")
    p.add_argument("scan_id")
    p.add_argument("--yes", action="store_true")

    p = add("audit", cmd_audit, "read the privileged-action log (or enact retention)")
    p.add_argument("--actor", help="only this account's actions (email or id)")
    p.add_argument("--action", help="exact action, e.g. scans.export or user.role")
    p.add_argument("--target", help="exact target id (an inspection or account)")
    p.add_argument("--since", metavar="WHEN",
                   help="age (30d, 24h, 90m), date (2026-08-01) or ISO timestamp")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--summary", action="store_true",
                   help="counts by action and actor instead of the entries")
    p.add_argument("--json", action="store_true",
                   help="machine-readable, with each entry's full detail")
    p.add_argument("--purge-before", metavar="WHEN",
                   help="DELETE entries older than this, to enact a retention period")
    p.add_argument("--yes", action="store_true", help="skip the purge confirmation")

    add("secret", cmd_secret, "generate a value for LABEL_JAANO_SECRET")

    p = add("seed", cmd_seed, "populate a demo corpus for a walkthrough")
    p.add_argument("--password", help="password for the demo accounts")
    p.add_argument("--password-stdin", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--force", action="store_true",
                   help="seed even though the database already holds inspections")
    p.add_argument("--yes", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_OK

    if args.db:
        store.configure(args.db)
    # Otherwise leave the path to store.db_path(): LABEL_JAANO_DB if set, else the
    # package default. Deliberately not re-derived here — an independent default in
    # this file would be a second source of truth, and the moment the two disagreed
    # the CLI and the server would quietly operate on different databases. That
    # failure looks like "the officer account I just created does not exist", which
    # is a miserable thing to debug. store.db_path() is already absolute, so it does
    # not matter which directory either process was started from.

    try:
        return args.func(args)
    except CommandError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("\ncancelled", file=sys.stderr)
        return EXIT_ERROR
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
