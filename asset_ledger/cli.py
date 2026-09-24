"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from . import __version__

DB_PATH = Path(__file__).resolve().parents[1] / "asset_ledger.sqlite3"

STATUS_STORED = "在库"
STATUS_IN_USE = "使用中"

TYPE_ACTIVATE = "activate"
TYPE_MOVE = "move"

DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
AMOUNT_RE = re.compile(r"^(?:0|[1-9]\d*)(?:\.\d{1,2})?$")
EFFECTIVE_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    asset_code            TEXT PRIMARY KEY,
    name                  TEXT NOT NULL,
    category              TEXT NOT NULL,
    purchase_date         TEXT NOT NULL,
    purchase_amount_cents INTEGER NOT NULL,
    status                TEXT NOT NULL,
    initial_location_code TEXT NOT NULL,
    initial_location_name TEXT,
    current_location_code TEXT NOT NULL,
    current_location_name TEXT,
    created_at            TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    seq                INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_code         TEXT NOT NULL,
    request_id         TEXT NOT NULL UNIQUE,
    type               TEXT NOT NULL,
    effective_at       TEXT NOT NULL,
    recorded_at        TEXT NOT NULL,
    from_location_code TEXT,
    from_location_name TEXT,
    to_location_code   TEXT,
    to_location_name   TEXT,
    reason             TEXT,
    payload_json       TEXT NOT NULL,
    response_json      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_asset ON events (asset_code, effective_at, seq);
"""


class LedgerError(Exception):
    """A business or validation failure with a stable error code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def fail(code: str, message: str) -> int:
    print(f"error: {code}: {message}", file=sys.stderr)
    return 1


def require(value: str | None, field: str) -> str:
    if value is None or value.strip() == "":
        raise LedgerError("missing-field", f"{field} is required")
    return value.strip()


def optional(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def parse_purchase_date(value: str) -> str:
    text = value.strip()
    if not DATE_RE.fullmatch(text):
        raise LedgerError("invalid-date", f"purchase_date must be YYYY-MM-DD: {value!r}")
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError as exc:
        raise LedgerError("invalid-date", f"invalid calendar date: {value!r}") from exc
    return text


def parse_amount(value: str) -> int:
    text = value.strip()
    if not AMOUNT_RE.fullmatch(text):
        raise LedgerError(
            "invalid-amount",
            f"purchase_amount must be positive with at most 2 decimal places: {value!r}",
        )
    whole, _, fraction = text.partition(".")
    cents = int(whole) * 100 + int((fraction + "00")[:2])
    if cents <= 0:
        raise LedgerError("invalid-amount", f"purchase_amount must be positive: {value!r}")
    return cents


def parse_effective_at(value: str) -> str:
    text = value.strip()
    for fmt in EFFECTIVE_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return parsed.strftime("%Y-%m-%dT%H:%M:%S")
    raise LedgerError(
        "invalid-effective-time",
        f"effective_at must be YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS: {value!r}",
    )


def amount_json(cents: int) -> int | float:
    value = cents / 100
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return round(value, 2)


def location_json(code: str | None, name: str | None) -> dict[str, str | None] | None:
    if code is None:
        return None
    return {"code": code, "name": name}


def asset_json(row: sqlite3.Row) -> dict[str, object]:
    return {
        "asset_code": row["asset_code"],
        "name": row["name"],
        "category": row["category"],
        "purchase_date": row["purchase_date"],
        "purchase_amount": amount_json(row["purchase_amount_cents"]),
        "status": row["status"],
        "current_location": location_json(
            row["current_location_code"], row["current_location_name"]
        ),
    }


def event_json(row: sqlite3.Row) -> dict[str, object]:
    return {
        "effective_at": row["effective_at"],
        "recorded_at": row["recorded_at"],
        "type": row["type"],
        "from_location": location_json(
            row["from_location_code"], row["from_location_name"]
        ),
        "to_location": location_json(row["to_location_code"], row["to_location_name"]),
        "reason": row["reason"],
        "request_id": row["request_id"],
    }


def dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def open_db() -> sqlite3.Connection:
    path = Path(os.environ.get("ASSET_LEDGER_DB") or DB_PATH)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def get_asset(conn: sqlite3.Connection, asset_code: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM assets WHERE asset_code = ?", (asset_code,)
    ).fetchone()
    if row is None:
        raise LedgerError("asset-not-found", f"no asset with code {asset_code!r}")
    return row


def find_event(conn: sqlite3.Connection, request_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM events WHERE request_id = ?", (request_id,)
    ).fetchone()


def check_replay(
    conn: sqlite3.Connection, request_id: str, payload: dict[str, object]
) -> str | None:
    """Return the stored response for an identical replay, None for a new request."""
    existing = find_event(conn, request_id)
    if existing is None:
        return None
    if existing["payload_json"] != dump(payload):
        raise LedgerError(
            "request-conflict",
            f"request_id {request_id!r} was already submitted with a different payload",
        )
    return existing["response_json"]


def safe_rollback(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("ROLLBACK")
    except sqlite3.Error:
        pass


def cmd_register(args: argparse.Namespace, conn: sqlite3.Connection) -> int:
    asset_code = require(args.asset_code, "--asset-code")
    name = require(args.name, "--name")
    category = require(args.category, "--category")
    purchase_date = parse_purchase_date(require(args.purchase_date, "--purchase-date"))
    amount_cents = parse_amount(require(args.purchase_amount, "--purchase-amount"))
    location_code = require(args.location_code, "--location-code")
    location_name = optional(args.location_name)

    created_at = datetime.now().isoformat(timespec="microseconds")
    conn.execute("BEGIN IMMEDIATE")
    try:
        existing = conn.execute(
            "SELECT 1 FROM assets WHERE asset_code = ?", (asset_code,)
        ).fetchone()
        if existing is not None:
            raise LedgerError("duplicate-asset", f"asset code already exists: {asset_code!r}")
        try:
            conn.execute(
                """
                INSERT INTO assets (
                    asset_code, name, category, purchase_date, purchase_amount_cents,
                    status, initial_location_code, initial_location_name,
                    current_location_code, current_location_name, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset_code,
                    name,
                    category,
                    purchase_date,
                    amount_cents,
                    STATUS_STORED,
                    location_code,
                    location_name,
                    location_code,
                    location_name,
                    created_at,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise LedgerError("duplicate-asset", f"asset code already exists: {asset_code!r}") from exc
        conn.execute("COMMIT")
    except Exception:
        safe_rollback(conn)
        raise

    row = get_asset(conn, asset_code)
    print(dump(asset_json(row)))
    return 0


def cmd_activate(args: argparse.Namespace, conn: sqlite3.Connection) -> int:
    asset_code = require(args.asset_code, "--asset-code")
    request_id = require(args.request_id, "--request-id")
    effective_at = parse_effective_at(require(args.effective_at, "--effective-at"))
    payload = {
        "command": TYPE_ACTIVATE,
        "asset_code": asset_code,
        "request_id": request_id,
        "effective_at": effective_at,
    }

    conn.execute("BEGIN IMMEDIATE")
    try:
        asset = get_asset(conn, asset_code)
        replayed = check_replay(conn, request_id, payload)
        if replayed is None:
            if asset["status"] != STATUS_STORED:
                raise LedgerError(
                    "invalid-status-transition",
                    f"activate requires status {STATUS_STORED!r}, got {asset['status']!r}",
                )
            if effective_at[:10] < asset["purchase_date"]:
                raise LedgerError(
                    "effective-before-purchase",
                    f"effective_at {effective_at} is earlier than purchase_date "
                    f"{asset['purchase_date']}",
                )
            recorded_at = datetime.now().isoformat(timespec="microseconds")
            response = dump(
                {
                    "asset_code": asset_code,
                    "name": asset["name"],
                    "category": asset["category"],
                    "purchase_date": asset["purchase_date"],
                    "purchase_amount": amount_json(asset["purchase_amount_cents"]),
                    "status": STATUS_IN_USE,
                    "current_location": location_json(
                        asset["current_location_code"], asset["current_location_name"]
                    ),
                }
            )
            conn.execute(
                """
                INSERT INTO events (
                    asset_code, request_id, type, effective_at, recorded_at,
                    from_location_code, from_location_name,
                    to_location_code, to_location_name,
                    reason, payload_json, response_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    asset_code,
                    request_id,
                    TYPE_ACTIVATE,
                    effective_at,
                    recorded_at,
                    asset["current_location_code"],
                    asset["current_location_name"],
                    asset["current_location_code"],
                    asset["current_location_name"],
                    dump(payload),
                    response,
                ),
            )
            conn.execute(
                "UPDATE assets SET status = ? WHERE asset_code = ?",
                (STATUS_IN_USE, asset_code),
            )
            conn.execute("COMMIT")
        else:
            conn.execute("COMMIT")
    except Exception:
        safe_rollback(conn)
        raise

    print(replayed if replayed is not None else response)
    return 0


def cmd_move(args: argparse.Namespace, conn: sqlite3.Connection) -> int:
    asset_code = require(args.asset_code, "--asset-code")
    request_id = require(args.request_id, "--request-id")
    effective_at = parse_effective_at(require(args.effective_at, "--effective-at"))
    to_code = require(args.to_location_code, "--to-location-code")
    to_name = optional(args.to_location_name)
    reason = require(args.reason, "--reason")
    payload = {
        "command": TYPE_MOVE,
        "asset_code": asset_code,
        "request_id": request_id,
        "effective_at": effective_at,
        "to_location_code": to_code,
        "to_location_name": to_name,
        "reason": reason,
    }

    conn.execute("BEGIN IMMEDIATE")
    try:
        asset = get_asset(conn, asset_code)
        replayed = check_replay(conn, request_id, payload)
        if replayed is None:
            if asset["status"] not in (STATUS_STORED, STATUS_IN_USE):
                raise LedgerError(
                    "invalid-status-transition",
                    f"move is not allowed in status {asset['status']!r}",
                )
            # The from-location is the predecessor on the effective-time
            # timeline (same effective time resolves by submission order, and
            # the new event always has the largest seq), or the registered
            # initial location when the late record predates every move.
            predecessor = conn.execute(
                """
                SELECT to_location_code, to_location_name FROM events
                WHERE asset_code = ? AND type = ? AND effective_at <= ?
                ORDER BY effective_at DESC, seq DESC LIMIT 1
                """,
                (asset_code, TYPE_MOVE, effective_at),
            ).fetchone()
            if predecessor is None:
                from_code = asset["initial_location_code"]
                from_name = asset["initial_location_name"]
            else:
                from_code = predecessor["to_location_code"]
                from_name = predecessor["to_location_name"]

            # Current location follows the move with the latest effective time
            # (ties resolved by submission order, where the new event wins).
            existing_latest = conn.execute(
                """
                SELECT effective_at, to_location_code, to_location_name FROM events
                WHERE asset_code = ? AND type = ?
                ORDER BY effective_at DESC, seq DESC LIMIT 1
                """,
                (asset_code, TYPE_MOVE),
            ).fetchone()
            if existing_latest is not None and existing_latest["effective_at"] > effective_at:
                current_code = existing_latest["to_location_code"]
                current_name = existing_latest["to_location_name"]
            else:
                current_code = to_code
                current_name = to_name

            recorded_at = datetime.now().isoformat(timespec="microseconds")
            response = dump(
                {
                    "asset_code": asset_code,
                    "name": asset["name"],
                    "category": asset["category"],
                    "purchase_date": asset["purchase_date"],
                    "purchase_amount": amount_json(asset["purchase_amount_cents"]),
                    "status": asset["status"],
                    "current_location": location_json(current_code, current_name),
                }
            )
            conn.execute(
                """
                INSERT INTO events (
                    asset_code, request_id, type, effective_at, recorded_at,
                    from_location_code, from_location_name,
                    to_location_code, to_location_name,
                    reason, payload_json, response_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset_code,
                    request_id,
                    TYPE_MOVE,
                    effective_at,
                    recorded_at,
                    from_code,
                    from_name,
                    to_code,
                    to_name,
                    reason,
                    dump(payload),
                    response,
                ),
            )
            conn.execute(
                "UPDATE assets SET current_location_code = ?, current_location_name = ?"
                " WHERE asset_code = ?",
                (current_code, current_name, asset_code),
            )
            conn.execute("COMMIT")
        else:
            conn.execute("COMMIT")
    except Exception:
        safe_rollback(conn)
        raise

    print(replayed if replayed is not None else response)
    return 0


def cmd_get(args: argparse.Namespace, conn: sqlite3.Connection) -> int:
    asset_code = require(args.asset_code, "--asset-code")
    row = get_asset(conn, asset_code)
    print(dump(asset_json(row)))
    return 0


def cmd_history(args: argparse.Namespace, conn: sqlite3.Connection) -> int:
    asset_code = require(args.asset_code, "--asset-code")
    get_asset(conn, asset_code)
    rows = conn.execute(
        "SELECT * FROM events WHERE asset_code = ? ORDER BY effective_at ASC, seq ASC",
        (asset_code,),
    ).fetchall()
    print(dump([event_json(row) for row in rows]))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="asset-ledger",
        description="Local 设备资产台账：登记、启用与位置变更（SQLite 本地持久化）。",
        epilog=(
            "业务命令:\n"
            "  register  登记资产（状态初始为“在库”）\n"
            "  activate  启用资产（“在库” -> “使用中”，携带 request_id 与生效时间）\n"
            "  move      位置变更（携带 request_id、生效时间与原因，同事务更新当前位置并追加移动历史）\n"
            "  get       按资产编号查询资产，输出一行 JSON\n"
            "  history   查询不可变事件历史（按生效时间升序），输出一行 JSON 数组\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="command")

    p_register = subparsers.add_parser(
        "register", help="登记新资产（编号唯一，初始状态为“在库”）"
    )
    p_register.add_argument("--asset-code", help="资产编号（稳定唯一）")
    p_register.add_argument("--name", help="资产名称（必填）")
    p_register.add_argument("--category", help="资产类别（必填）")
    p_register.add_argument("--purchase-date", help="采购日期 YYYY-MM-DD（必填）")
    p_register.add_argument("--purchase-amount", help="采购金额，正数且最多两位小数（必填）")
    p_register.add_argument("--location-code", help="初始位置编码（必填）")
    p_register.add_argument("--location-name", help="初始位置名称（可选）")

    p_activate = subparsers.add_parser(
        "activate", help="启用资产（仅“在库”可启用，需 request_id 与生效时间）"
    )
    p_activate.add_argument("--asset-code", help="资产编号")
    p_activate.add_argument("--request-id", help="本次业务的唯一请求 ID（支持同载荷重放）")
    p_activate.add_argument("--effective-at", help="业务生效时间 YYYY-MM-DD 或 YYYY-MM-DDTHH:MM:SS")

    p_move = subparsers.add_parser(
        "move", help="变更资产位置（在库/使用中均可，追加不可变移动历史）"
    )
    p_move.add_argument("--asset-code", help="资产编号")
    p_move.add_argument("--request-id", help="本次业务的唯一请求 ID（支持同载荷重放）")
    p_move.add_argument("--effective-at", help="业务生效时间 YYYY-MM-DD 或 YYYY-MM-DDTHH:MM:SS")
    p_move.add_argument("--to-location-code", help="目标位置编码（必填）")
    p_move.add_argument("--to-location-name", help="目标位置名称（可选）")
    p_move.add_argument("--reason", help="位置变更原因（必填）")

    p_get = subparsers.add_parser("get", help="按资产编号查询资产（一行 JSON）")
    p_get.add_argument("--asset-code", help="资产编号")

    p_history = subparsers.add_parser(
        "history", help="查询事件历史（一行 JSON 数组，按生效时间升序）"
    )
    p_history.add_argument("--asset-code", help="资产编号")

    return parser


COMMANDS = {
    "register": cmd_register,
    "activate": cmd_activate,
    "move": cmd_move,
    "get": cmd_get,
    "history": cmd_history,
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    conn: sqlite3.Connection | None = None
    try:
        conn = open_db()
        return COMMANDS[args.command](args, conn)
    except LedgerError as exc:
        if conn is not None:
            safe_rollback(conn)
        return fail(exc.code, exc.message)
    except sqlite3.Error as exc:
        if conn is not None:
            safe_rollback(conn)
        return fail("database-error", str(exc))
    finally:
        if conn is not None:
            conn.close()
