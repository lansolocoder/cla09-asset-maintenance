"""Command-line entry point."""

import argparse
from collections.abc import Sequence
from datetime import date, datetime
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
