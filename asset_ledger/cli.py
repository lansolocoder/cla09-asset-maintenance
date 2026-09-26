"""Command-line entry point."""

import argparse
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import sqlite3
import sys

from . import __version__

DB_PATH = Path(
    os.environ.get(
        "ASSET_LEDGER_DB",
        Path(__file__).resolve().parents[1] / "ledger.db",
    )
)

STATUSES = ("in_use", "repairing", "retired")

# Legal forward transitions; ``retired`` is terminal and self-transitions
# are never allowed.
TRANSITIONS = {
    "in_use": {"repairing", "retired"},
    "repairing": {"in_use", "retired"},
    "retired": set(),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    cost TEXT NOT NULL,
    purchased_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id TEXT NOT NULL REFERENCES assets(id),
    at TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT
);
CREATE TABLE IF NOT EXISTS plans (
    id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES assets(id),
    cycle_days INTEGER NOT NULL,
    first_due TEXT NOT NULL
);
"""

_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")


class LedgerError(Exception):
    """A rejected command: report on stderr and exit 1."""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    return conn


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _parse_cost(text: str) -> str:
    try:
        cost = Decimal(text)
    except InvalidOperation:
        raise LedgerError(f"invalid cost: {text!r}") from None
    if not cost.is_finite() or cost <= 0:
        raise LedgerError(f"invalid cost: {text!r}")
    return str(cost)


def _parse_purchased_at(text: str) -> str:
    if not _DATE_PATTERN.fullmatch(text):
        raise LedgerError(f"invalid purchased-at date: {text!r} (expected YYYY-MM-DD)")
    try:
        purchased = date.fromisoformat(text)
    except ValueError:
        raise LedgerError(f"invalid purchased-at date: {text!r}") from None
    if purchased > date.today():
        raise LedgerError(f"purchased-at date is in the future: {text!r}")
    return text


def _parse_date(text: str, label: str) -> date:
    if not _DATE_PATTERN.fullmatch(text):
        raise LedgerError(f"invalid {label} date: {text!r} (expected YYYY-MM-DD)")
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise LedgerError(f"invalid {label} date: {text!r}") from None


def _parse_first_due(text: str) -> str:
    first_due = _parse_date(text, "first-due")
    if first_due > date.today():
        raise LedgerError(f"first-due date is in the future: {text!r}")
    return text


def _parse_cycle_days(text: str) -> int:
    try:
        days = int(text)
    except ValueError:
        raise LedgerError(
            f"invalid cycle days: {text!r} (expected a positive integer)"
        ) from None
    if days <= 0:
        raise LedgerError(f"invalid cycle days: {text!r} (expected a positive integer)")
    return days


def _next_due_date(first_due: date, cycle_days: int, today: date) -> date:
    """Roll ``first_due`` forward by whole cycles to the first date not yet
    consumed, i.e. the smallest due date on or after ``today``."""
    elapsed = (today - first_due).days
    cycles = max(0, -(-elapsed // cycle_days))  # ceil division, never negative
    return first_due + timedelta(days=cycles * cycle_days)


def _cost_as_number(text: str) -> int | float:
    value = Decimal(text)
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def _current_status(conn: sqlite3.Connection, asset_id: str) -> str | None:
    row = conn.execute(
        "SELECT status FROM events WHERE asset_id = ? ORDER BY seq DESC LIMIT 1",
        (asset_id,),
    ).fetchone()
    return row[0] if row else None


def _cmd_add(args: argparse.Namespace) -> int:
    cost = _parse_cost(args.cost)
    purchased_at = _parse_purchased_at(args.purchased_at)
    conn = _connect()
    try:
        with conn:  # rolls back if the duplicate check raises
            if conn.execute(
                "SELECT 1 FROM assets WHERE id = ?", (args.id,)
            ).fetchone():
                raise LedgerError(f"asset {args.id} already exists")
            try:
                conn.execute(
                    "INSERT INTO assets (id, name, cost, purchased_at)"
                    " VALUES (?, ?, ?, ?)",
                    (args.id, args.name, cost, purchased_at),
                )
                conn.execute(
                    "INSERT INTO events (asset_id, at, status, reason)"
                    " VALUES (?, ?, ?, ?)",
                    (args.id, _now(), "in_use", None),
                )
            except sqlite3.IntegrityError:
                raise LedgerError(f"asset {args.id} already exists") from None
    finally:
        conn.close()
    print(f"asset {args.id} registered")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    target = args.to
    if target not in STATUSES:
        raise LedgerError(
            f"invalid status: {target!r} (expected one of {', '.join(STATUSES)})"
        )
    reason = args.reason.strip() if args.reason is not None else None
    if target == "retired" and not reason:
        raise LedgerError("retiring an asset requires a non-empty --reason")
    if not reason:
        reason = None
    if not DB_PATH.exists():
        raise LedgerError(f"asset {args.id} not found")
    conn = _connect()
    try:
        with conn:
            current = _current_status(conn, args.id)
            if current is None:
                raise LedgerError(f"asset {args.id} not found")
            if target not in TRANSITIONS[current]:
                raise LedgerError(
                    f"cannot transition asset {args.id} from {current} to {target}"
                )
            conn.execute(
                "INSERT INTO events (asset_id, at, status, reason) VALUES (?, ?, ?, ?)",
                (args.id, _now(), target, reason),
            )
    finally:
        conn.close()
    print(f"asset {args.id} {target}")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    if not DB_PATH.exists():
        raise LedgerError(f"asset {args.id} not found")
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT id, name, cost, purchased_at FROM assets WHERE id = ?",
            (args.id,),
        ).fetchone()
        if row is None:
            raise LedgerError(f"asset {args.id} not found")
        events = conn.execute(
            "SELECT at, status, reason FROM events WHERE asset_id = ? ORDER BY seq",
            (args.id,),
        ).fetchall()
    finally:
        conn.close()
    payload = {
        "id": row[0],
        "name": row[1],
        "cost": _cost_as_number(row[2]),
        "purchased_at": row[3],
        "status": events[-1][1],
        "history": [
            {"at": at, "status": status, "reason": reason}
            for at, status, reason in events
        ],
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def _cmd_plan_add(args: argparse.Namespace) -> int:
    cycle_days = _parse_cycle_days(args.cycle_days)
    first_due = _parse_first_due(args.first_due)
    if not DB_PATH.exists():
        raise LedgerError(f"asset {args.asset_id} not found")
    conn = _connect()
    try:
        with conn:  # rolls back if any check raises
            if not conn.execute(
                "SELECT 1 FROM assets WHERE id = ?", (args.asset_id,)
            ).fetchone():
                raise LedgerError(f"asset {args.asset_id} not found")
            if conn.execute(
                "SELECT 1 FROM plans WHERE id = ?", (args.id,)
            ).fetchone():
                raise LedgerError(f"plan {args.id} already exists")
            try:
                conn.execute(
                    "INSERT INTO plans (id, asset_id, cycle_days, first_due)"
                    " VALUES (?, ?, ?, ?)",
                    (args.id, args.asset_id, cycle_days, first_due),
                )
            except sqlite3.IntegrityError:
                raise LedgerError(f"plan {args.id} already exists") from None
    finally:
        conn.close()
    print(f"plan {args.id} created")
    return 0


def _cmd_plan_due(args: argparse.Namespace) -> int:
    as_of = _parse_date(args.as_of, "as-of")
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, asset_id, cycle_days, first_due FROM plans"
        ).fetchall()
        statuses = {
            asset_id: _current_status(conn, asset_id)
            for asset_id in {row[1] for row in rows}
        }
    finally:
        conn.close()
    today = date.today()
    due = []
    for plan_id, asset_id, cycle_days, first_due in rows:
        status = statuses[asset_id]
        if status == "retired":
            continue  # retired assets never appear in the due list
        next_due = _next_due_date(date.fromisoformat(first_due), cycle_days, today)
        if next_due > as_of:
            continue
        due.append(
            {
                "plan_id": plan_id,
                "asset_id": asset_id,
                "due_date": next_due.isoformat(),
                "asset_status": status,
            }
        )
    due.sort(key=lambda entry: (entry["due_date"], entry["plan_id"]))
    print(json.dumps({"as_of": args.as_of, "due": due}, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="asset-ledger",
        description="Local 设备资产与维保台账 ledger.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    asset = subparsers.add_parser("asset", help="manage registered assets")
    asset_subparsers = asset.add_subparsers(dest="asset_command")

    add = asset_subparsers.add_parser("add", help="register a new asset")
    add.add_argument("--id", required=True, help="unique asset identifier")
    add.add_argument("--name", required=True, help="asset name")
    add.add_argument("--cost", required=True, help="positive purchase cost")
    add.add_argument("--purchased-at", required=True, help="purchase date, YYYY-MM-DD")
    add.set_defaults(handler=_cmd_add)

    status = asset_subparsers.add_parser("status", help="transition an asset's status")
    status.add_argument("--id", required=True, help="asset identifier")
    status.add_argument("--to", required=True, help="target status")
    status.add_argument("--reason", help="required when retiring an asset")
    status.set_defaults(handler=_cmd_status)

    show = asset_subparsers.add_parser("show", help="show one asset as JSON")
    show.add_argument("--id", required=True, help="asset identifier")
    show.set_defaults(handler=_cmd_show)

    plan = subparsers.add_parser("plan", help="manage periodic maintenance plans")
    plan_subparsers = plan.add_subparsers(dest="plan_command")

    plan_add = plan_subparsers.add_parser(
        "add", help="register a maintenance plan for an asset"
    )
    plan_add.add_argument("--id", required=True, help="unique plan identifier")
    plan_add.add_argument("--asset-id", required=True, help="asset to maintain")
    plan_add.add_argument(
        "--cycle-days", required=True, help="maintenance cycle in days, positive integer"
    )
    plan_add.add_argument(
        "--first-due",
        required=True,
        help="first due date, YYYY-MM-DD, not later than today",
    )
    plan_add.set_defaults(handler=_cmd_plan_add)

    due = plan_subparsers.add_parser(
        "due", help="list plans whose next due date falls on or before a cutoff"
    )
    due.add_argument("--as-of", required=True, help="cutoff date, YYYY-MM-DD")
    due.set_defaults(handler=_cmd_plan_due)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0
    try:
        return handler(args)
    except LedgerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
