"""Command-line entry point."""

import argparse
import json
import math
import sqlite3
import sys
from collections.abc import Sequence
from datetime import date, datetime, timezone
from pathlib import Path

from . import __version__

DB_PATH = Path(__file__).resolve().parents[1] / "ledger.db"

STATUSES = ("in_use", "repairing", "retired")
TRANSITIONS = {
    "in_use": {"repairing", "retired"},
    "repairing": {"in_use", "retired"},
    "retired": set(),
}


def fail(message: str) -> int:
    """Report a rejected command; nothing is written by the caller."""
    print(f"error: {message}", file=sys.stderr)
    return 1


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS assets (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            cost REAL NOT NULL,
            purchased_at TEXT NOT NULL,
            status TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS history (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_id TEXT NOT NULL REFERENCES assets(id),
            at TEXT NOT NULL,
            status TEXT NOT NULL,
            reason TEXT
        );
        """
    )
    return conn


def cmd_add(args: argparse.Namespace) -> int:
    try:
        cost = float(args.cost)
    except (TypeError, ValueError):
        return fail(f"invalid cost {args.cost!r}: must be a positive number")
    if not math.isfinite(cost) or cost <= 0:
        return fail(f"invalid cost {args.cost!r}: must be a positive number")

    try:
        purchased = datetime.strptime(args.purchased_at, "%Y-%m-%d").date()
    except ValueError:
        return fail(
            f"invalid purchased-at {args.purchased_at!r}: "
            "must be YYYY-MM-DD"
        )
    if purchased > date.today():
        return fail("invalid purchased-at: date must not be later than today")

    conn = connect()
    try:
        exists = conn.execute(
            "SELECT 1 FROM assets WHERE id = ?", (args.id,)
        ).fetchone()
        if exists is not None:
            return fail(f"asset {args.id} already exists")
        timestamp = now_iso()
        with conn:
            conn.execute(
                "INSERT INTO assets (id, name, cost, purchased_at, status)"
                " VALUES (?, ?, ?, ?, 'in_use')",
                (args.id, args.name, cost, purchased.isoformat()),
            )
            conn.execute(
                "INSERT INTO history (asset_id, at, status, reason)"
                " VALUES (?, ?, 'in_use', NULL)",
                (args.id, timestamp),
            )
    finally:
        conn.close()

    print(f"asset {args.id} registered")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    if args.to not in STATUSES:
        allowed = ", ".join(STATUSES)
        return fail(f"invalid status {args.to!r}: must be one of {allowed}")

    conn = connect()
    try:
        row = conn.execute(
            "SELECT status FROM assets WHERE id = ?", (args.id,)
        ).fetchone()
        if row is None:
            return fail(f"asset {args.id} not found")
        current = row[0]

        if args.to == current or args.to not in TRANSITIONS[current]:
            return fail(f"illegal transition: {current} -> {args.to}")
        if args.to == "retired" and (args.reason is None or not args.reason.strip()):
            return fail("reason is required when retiring an asset")

        timestamp = now_iso()
        with conn:
            conn.execute(
                "INSERT INTO history (asset_id, at, status, reason)"
                " VALUES (?, ?, ?, ?)",
                (args.id, timestamp, args.to, args.reason),
            )
            conn.execute(
                "UPDATE assets SET status = ? WHERE id = ?",
                (args.to, args.id),
            )
    finally:
        conn.close()

    print(f"asset {args.id} {args.to}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    conn = connect()
    try:
        row = conn.execute(
            "SELECT id, name, cost, purchased_at, status"
            " FROM assets WHERE id = ?",
            (args.id,),
        ).fetchone()
        if row is None:
            return fail(f"asset {args.id} not found")
        history = conn.execute(
            "SELECT at, status, reason FROM history"
            " WHERE asset_id = ? ORDER BY seq",
            (args.id,),
        ).fetchall()
    finally:
        conn.close()

    payload = {
        "id": row[0],
        "name": row[1],
        "cost": row[2],
        "purchased_at": row[3],
        "status": row[4],
        "history": [
            {"at": at, "status": status, "reason": reason}
            for at, status, reason in history
        ],
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="asset-ledger",
        description="Local 设备资产与维保台账 ledger.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )

    asset_parser = parser.add_subparsers(dest="command").add_parser(
        "asset", help="manage the asset ledger"
    )
    action_parsers = asset_parser.add_subparsers(dest="action", required=True)

    add_parser = action_parsers.add_parser("add", help="register an asset")
    add_parser.add_argument("--id", required=True)
    add_parser.add_argument("--name", required=True)
    add_parser.add_argument("--cost", required=True)
    add_parser.add_argument("--purchased-at", dest="purchased_at", required=True)
    add_parser.set_defaults(func=cmd_add)

    status_parser = action_parsers.add_parser(
        "status", help="move an asset to another status"
    )
    status_parser.add_argument("--id", required=True)
    status_parser.add_argument("--to", required=True)
    status_parser.add_argument("--reason", default=None)
    status_parser.set_defaults(func=cmd_status)

    show_parser = action_parsers.add_parser("show", help="show an asset")
    show_parser.add_argument("--id", required=True)
    show_parser.set_defaults(func=cmd_show)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    return args.func(args)
