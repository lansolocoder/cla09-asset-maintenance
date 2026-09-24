"""SQLite-backed storage for assets and their location-change history.

The data file is created on the first successful registration. All writes
happen inside transactions so a failed validation never leaves a partial
record behind.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Sequence
from datetime import date
from pathlib import Path

_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tag             TEXT NOT NULL UNIQUE,
    name            TEXT NOT NULL,
    category        TEXT NOT NULL,
    purchase_date   TEXT NOT NULL,
    current_location TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS location_changes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id    INTEGER NOT NULL REFERENCES assets(id),
    change_date TEXT NOT NULL,
    from_location TEXT NOT NULL,
    to_location TEXT NOT NULL,
    note        TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS register_requests (
    request_id    TEXT PRIMARY KEY,
    asset_id      INTEGER NOT NULL REFERENCES assets(id),
    tag           TEXT NOT NULL,
    name          TEXT NOT NULL,
    category      TEXT NOT NULL,
    purchase_date TEXT NOT NULL,
    location      TEXT NOT NULL
);
"""


class LedgerError(Exception):
    """A business-rule or validation failure reported to the user."""


class IdempotencyConflict(LedgerError):
    """A request id was reused with different registration content."""


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def initialize(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def _parse_date(value: str, field: str) -> date:
    match = _DATE_RE.match(value)
    if match is None:
        raise LedgerError(f"{field} 必须是合法日期，格式 YYYY-MM-DD: {value!r}")
    try:
        return date(int(match[1]), int(match[2]), int(match[3]))
    except ValueError:
        raise LedgerError(f"{field} 必须是合法日期，格式 YYYY-MM-DD: {value!r}")


def _require(value: str | None, field: str) -> str:
    if value is None or value.strip() == "":
        raise LedgerError(f"缺少必填项: {field}")
    return value.strip()


def validate_registration(
    tag: str | None,
    name: str | None,
    category: str | None,
    purchase_date: str | None,
    location: str | None,
) -> tuple[str, str, str, str, str]:
    """Validate registration fields without touching the database."""
    tag = _require(tag, "资产标签")
    name = _require(name, "名称")
    category = _require(category, "类别")
    purchase_date = _require(purchase_date, "购置日期")
    location = _require(location, "初始存放位置")
    _parse_date(purchase_date, "购置日期")
    return tag, name, category, purchase_date, location


def register_asset(
    conn: sqlite3.Connection,
    *,
    tag: str | None,
    name: str | None,
    category: str | None,
    purchase_date: str | None,
    location: str | None,
    request_id: str | None = None,
) -> tuple[int, str, str]:
    """Register one asset. Returns (ledger id, tag, initial location).

    Safe to retry with the same ``request_id``: a repeated identical request
    returns the original result without creating a second asset; a repeated
    request with different content is rejected.
    """
    tag, name, category, purchase_date, location = validate_registration(
        tag, name, category, purchase_date, location
    )

    try:
        with conn:  # transaction: rolls back on any exception
            if request_id is not None:
                row = conn.execute(
                    "SELECT * FROM register_requests WHERE request_id = ?",
                    (request_id,),
                ).fetchone()
                if row is not None:
                    stored = (
                        row["tag"],
                        row["name"],
                        row["category"],
                        row["purchase_date"],
                        row["location"],
                    )
                    if stored != (tag, name, category, purchase_date, location):
                        raise IdempotencyConflict(
                            f"请求标识 {request_id!r} 已用于内容不同的登记请求，"
                            "不能重复使用同一请求标识提交不同内容"
                        )
                    return row["asset_id"], row["tag"], row["location"]

            if conn.execute(
                "SELECT 1 FROM assets WHERE tag = ?", (tag,)
            ).fetchone():
                raise LedgerError(f"资产标签重复: {tag!r}")

            cur = conn.execute(
                "INSERT INTO assets (tag, name, category, purchase_date,"
                " current_location) VALUES (?, ?, ?, ?, ?)",
                (tag, name, category, purchase_date, location),
            )
            asset_id = int(cur.lastrowid)
            if request_id is not None:
                conn.execute(
                    "INSERT INTO register_requests (request_id, asset_id, tag,"
                    " name, category, purchase_date, location)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        request_id,
                        asset_id,
                        tag,
                        name,
                        category,
                        purchase_date,
                        location,
                    ),
                )
            return asset_id, tag, location
    except sqlite3.IntegrityError as exc:
        # UNIQUE(tag) raced with a concurrent insert; treat as duplicate.
        raise LedgerError(f"资产标签重复: {tag!r}") from exc


def validate_move(
    tag: str | None, location: str | None, change_date: str | None
) -> tuple[str, str, str]:
    """Validate move fields without touching the database."""
    tag = _require(tag, "资产标签")
    location = _require(location, "目标存放位置")
    change_date = _require(change_date, "变更日期")
    _parse_date(change_date, "变更日期")
    return tag, location, change_date


def change_location(
    conn: sqlite3.Connection,
    *,
    tag: str | None,
    location: str | None,
    change_date: str | None,
    note: str | None = None,
) -> tuple[str, str]:
    """Move an asset to a new location. Returns (tag, new location)."""
    tag, location, change_date = validate_move(tag, location, change_date)
    note = (note or "").strip()

    with conn:
        asset = conn.execute(
            "SELECT * FROM assets WHERE tag = ?", (tag,)
        ).fetchone()
        if asset is None:
            raise LedgerError(f"目标资产不存在: {tag!r}")

        if asset["current_location"] == location:
            raise LedgerError(
                f"目标存放位置与当前位置相同: {location!r}，未发生变更"
            )

        latest = conn.execute(
            "SELECT change_date FROM location_changes WHERE asset_id = ?"
            " ORDER BY id DESC LIMIT 1",
            (asset["id"],),
        ).fetchone()
        if latest is not None and change_date < latest["change_date"]:
            raise LedgerError(
                f"变更日期 {change_date} 早于最近一次变更日期"
                f" {latest['change_date']}，操作被拒绝"
            )

        conn.execute(
            "INSERT INTO location_changes (asset_id, change_date,"
            " from_location, to_location, note) VALUES (?, ?, ?, ?, ?)",
            (
                asset["id"],
                change_date,
                asset["current_location"],
                location,
                note,
            ),
        )
        conn.execute(
            "UPDATE assets SET current_location = ? WHERE id = ?",
            (location, asset["id"]),
        )
        return tag, location


def get_asset(conn: sqlite3.Connection, tag: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM assets WHERE tag = ?", (tag,)).fetchone()


def get_history(conn: sqlite3.Connection, asset_id: int) -> Sequence[sqlite3.Row]:
    return conn.execute(
        "SELECT change_date, from_location, to_location, note"
        " FROM location_changes WHERE asset_id = ? ORDER BY id",
        (asset_id,),
    ).fetchall()


def list_assets(
    conn: sqlite3.Connection, category: str | None = None
) -> Sequence[sqlite3.Row]:
    if category is not None:
        return conn.execute(
            "SELECT * FROM assets WHERE category = ? ORDER BY id", (category,)
        ).fetchall()
    return conn.execute("SELECT * FROM assets ORDER BY id").fetchall()
