"""SQLite-backed storage for assets and their location-change history.

The data file is created on the first successful registration. All writes
happen inside transactions so a failed validation never leaves a partial
record behind.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Sequence
from datetime import date, timedelta
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

CREATE TABLE IF NOT EXISTS maintenance_plans (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id    INTEGER NOT NULL REFERENCES assets(id),
    name        TEXT NOT NULL,
    cycle_days  INTEGER,
    start_date  TEXT,
    next_due    TEXT NOT NULL,
    UNIQUE (asset_id, name)
);

CREATE TABLE IF NOT EXISTS plan_requests (
    request_id  TEXT PRIMARY KEY,
    plan_id     INTEGER NOT NULL REFERENCES maintenance_plans(id),
    tag         TEXT NOT NULL,
    name        TEXT NOT NULL,
    cycle_days  INTEGER,
    start_date  TEXT,
    next_due    TEXT NOT NULL
);
"""

DUE_SOON_DAYS = 7


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


def validate_plan(
    tag: str | None,
    name: str | None,
    next_due: str | None,
    cycle_days: int | None,
    start_date: str | None,
) -> tuple[str, str, int | None, str | None, str]:
    """Validate maintenance-plan fields without touching the database.

    Returns (tag, name, cycle_days, start_date, next_due) with dates as
    ``YYYY-MM-DD`` strings. The due date comes either directly from
    ``--next-due`` or is computed as ``start_date + cycle_days``.
    """
    tag = _require(tag, "资产标签")
    name = _require(name, "计划名称")
    direct = next_due is not None and next_due.strip() != ""
    cycle_given = cycle_days is not None
    start_given = start_date is not None and start_date.strip() != ""
    if direct and (cycle_given or start_given):
        raise LedgerError(
            "--next-due 与 --cycle-days/--start-date 只能二选一，不能同时提供"
        )
    if not direct and not (cycle_given and start_given):
        raise LedgerError(
            "请提供 --next-due，或同时提供 --cycle-days 与 --start-date"
        )
    if direct:
        due = _parse_date(next_due.strip(), "下次到期日期")
        return tag, name, None, None, due.isoformat()
    if cycle_days <= 0:
        raise LedgerError(f"--cycle-days 必须是正整数: {cycle_days!r}")
    start = _parse_date(start_date.strip(), "起算日期")
    due = start + timedelta(days=cycle_days)
    return tag, name, cycle_days, start.isoformat(), due.isoformat()


def add_plan(
    conn: sqlite3.Connection,
    *,
    tag: str | None,
    name: str | None,
    next_due: str | None = None,
    cycle_days: int | None = None,
    start_date: str | None = None,
    request_id: str | None = None,
) -> tuple[int, str, str, str]:
    """Create a maintenance plan. Returns (plan id, tag, name, next due date).

    Safe to retry with the same ``request_id``: a repeated identical request
    returns the original result without creating a second plan; a repeated
    request with different content is rejected.
    """
    tag, name, cycle_days, start_date, due = validate_plan(
        tag, name, next_due, cycle_days, start_date
    )

    try:
        with conn:  # transaction: rolls back on any exception
            if request_id is not None:
                row = conn.execute(
                    "SELECT * FROM plan_requests WHERE request_id = ?",
                    (request_id,),
                ).fetchone()
                if row is not None:
                    stored = (
                        row["tag"],
                        row["name"],
                        row["cycle_days"],
                        row["start_date"],
                        row["next_due"],
                    )
                    if stored != (tag, name, cycle_days, start_date, due):
                        raise IdempotencyConflict(
                            f"请求标识 {request_id!r} 已用于内容不同的维保计划，"
                            "不能重复使用同一请求标识提交不同内容"
                        )
                    return row["plan_id"], row["tag"], row["name"], row["next_due"]

            asset = conn.execute(
                "SELECT * FROM assets WHERE tag = ?", (tag,)
            ).fetchone()
            if asset is None:
                raise LedgerError(f"目标资产不存在: {tag!r}")

            if conn.execute(
                "SELECT 1 FROM maintenance_plans WHERE asset_id = ? AND name = ?",
                (asset["id"], name),
            ).fetchone():
                raise LedgerError(f"该资产下计划名称重复: {name!r}")

            cur = conn.execute(
                "INSERT INTO maintenance_plans (asset_id, name, cycle_days,"
                " start_date, next_due) VALUES (?, ?, ?, ?, ?)",
                (asset["id"], name, cycle_days, start_date, due),
            )
            plan_id = int(cur.lastrowid)
            if request_id is not None:
                conn.execute(
                    "INSERT INTO plan_requests (request_id, plan_id, tag, name,"
                    " cycle_days, start_date, next_due)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (request_id, plan_id, tag, name, cycle_days, start_date, due),
                )
            return plan_id, tag, name, due
    except sqlite3.IntegrityError as exc:
        # UNIQUE(asset_id, name) raced with a concurrent insert.
        raise LedgerError(f"该资产下计划名称重复: {name!r}") from exc


def list_plans(conn: sqlite3.Connection, asset_id: int) -> Sequence[sqlite3.Row]:
    return conn.execute(
        "SELECT name, cycle_days, next_due FROM maintenance_plans"
        " WHERE asset_id = ? ORDER BY name",
        (asset_id,),
    ).fetchall()


def validate_as_of(value: str | None) -> date:
    """Validate the plan-due reference date without touching the database."""
    return _parse_date(_require(value, "基准日期"), "基准日期")


def due_plans(
    conn: sqlite3.Connection, as_of: date, window_days: int = DUE_SOON_DAYS
) -> list[dict]:
    """Plans due on/before ``as_of`` or within ``window_days`` after it.

    Each entry carries tag, plan name, due date, status and remaining days
    (due date minus ``as_of``); sorted by remaining days, then ledger id,
    then plan name.
    """
    rows = conn.execute(
        "SELECT p.name AS plan_name, p.next_due, a.id AS asset_id, a.tag"
        " FROM maintenance_plans p JOIN assets a ON a.id = p.asset_id"
    ).fetchall()
    results = []
    for row in rows:
        due = date.fromisoformat(row["next_due"])
        remaining = (due - as_of).days
        if remaining <= 0:
            status = "已到期"
        elif remaining <= window_days:
            status = "即将到期"
        else:
            continue
        results.append(
            {
                "tag": row["tag"],
                "plan_name": row["plan_name"],
                "next_due": row["next_due"],
                "status": status,
                "remaining": remaining,
                "asset_id": row["asset_id"],
            }
        )
    results.sort(key=lambda r: (r["remaining"], r["asset_id"], r["plan_name"]))
    return results
