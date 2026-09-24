"""SQLite-backed storage for the local asset ledger.

Everything in this module uses stable English tokens for machine-readable
state (``in_stock`` / ``in_use``) and event types (``registration`` /
``activation`` / ``move``).  Every state-changing callable runs in a single
transaction: a validation failure or a database error rolls the whole
operation back, so current state and event history never diverge.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

STATUS_IN_STOCK = "in_stock"
STATUS_IN_USE = "in_use"

TYPE_REGISTRATION = "registration"
TYPE_ACTIVATION = "activation"
TYPE_MOVE = "move"

ACTION_ACTIVATE = "activate"
ACTION_MOVE = "move"

SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    asset_code             TEXT PRIMARY KEY,
    name                   TEXT NOT NULL,
    category               TEXT NOT NULL,
    purchase_date          TEXT NOT NULL,
    purchase_amount_cents  INTEGER NOT NULL,
    status                 TEXT NOT NULL,
    current_location_code  TEXT NOT NULL,
    current_location_name  TEXT,
    created_at             TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    seq                INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_code         TEXT NOT NULL REFERENCES assets(asset_code),
    type               TEXT NOT NULL,
    effective_at       TEXT NOT NULL,
    recorded_at        TEXT NOT NULL,
    from_location_code TEXT,
    from_location_name TEXT,
    to_location_code   TEXT,
    to_location_name   TEXT,
    reason             TEXT,
    request_id         TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_asset ON events(asset_code);

CREATE TABLE IF NOT EXISTS requests (
    request_id   TEXT PRIMARY KEY,
    asset_code   TEXT NOT NULL,
    action       TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    response     TEXT NOT NULL
);
"""

_AMOUNT_RE = re.compile(r"^\d+(?:\.\d{1,2})?$")
_DATETIME_FORMATS = ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S")


class LedgerError(Exception):
    """A stable, user-facing ledger error.

    ``code`` is a stable kebab-case identifier printed to stderr and used by
    callers (and tests) instead of parsing prose.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def default_db_path() -> Path:
    """Return the database path, overridable via ``ASSET_LEDGER_DB``."""
    override = os.environ.get("ASSET_LEDGER_DB")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[1] / "asset_ledger.sqlite3"


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def _now() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def parse_purchase_date(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except (ValueError, TypeError):
        raise LedgerError(
            "validation-error",
            f"非法采购日期: {value!r}，需为 YYYY-MM-DD",
        )


def parse_amount_cents(value: str) -> int:
    if not isinstance(value, str) or not _AMOUNT_RE.match(value):
        raise LedgerError(
            "validation-error",
            f"非法采购金额: {value!r}，需为正数且最多两位小数",
        )
    whole, _, fraction = value.partition(".")
    cents = int(whole) * 100 + int((fraction + "00")[:2])
    if cents <= 0:
        raise LedgerError(
            "validation-error",
            f"非法采购金额: {value!r}，需为正数",
        )
    return cents


def parse_effective_at(value: str) -> datetime:
    for fmt in _DATETIME_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except (ValueError, TypeError):
            continue
    raise LedgerError(
        "validation-error",
        f"非法生效时间: {value!r}，需为 YYYY-MM-DDTHH:MM:SS",
    )


def require_nonempty(field: str, value: str | None) -> str:
    if value is None or value.strip() == "":
        raise LedgerError("validation-error", f"必填字段缺失: {field}")
    return value.strip()


def _payload_hash(action: str, payload: dict[str, Any]) -> str:
    raw = json.dumps(
        {"action": action, **payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _location(code: str | None, name: str | None) -> dict[str, Any] | None:
    if code is None:
        return None
    return {"code": code, "name": name}


def _asset_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "asset_code": row["asset_code"],
        "name": row["name"],
        "category": row["category"],
        "purchase_date": row["purchase_date"],
        "purchase_amount": row["purchase_amount_cents"] / 100,
        "status": row["status"],
        "current_location": _location(
            row["current_location_code"], row["current_location_name"]
        ),
    }


def _event_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "effective_at": row["effective_at"],
        "recorded_at": row["recorded_at"],
        "type": row["type"],
        "from_location": _location(
            row["from_location_code"], row["from_location_name"]
        ),
        "to_location": _location(row["to_location_code"], row["to_location_name"]),
        "reason": row["reason"],
        "request_id": row["request_id"],
    }


def register_asset(
    conn: sqlite3.Connection,
    *,
    asset_code: str,
    name: str,
    category: str,
    purchase_date: str,
    purchase_amount: str,
    location_code: str,
    location_name: str | None,
) -> dict[str, Any]:
    """Validate and persist a new asset plus its registration event."""
    asset_code = require_nonempty("asset_code", asset_code)
    name = require_nonempty("name", name)
    category = require_nonempty("category", category)
    purchase_date_iso = parse_purchase_date(
        require_nonempty("purchase_date", purchase_date)
    )
    amount_cents = parse_amount_cents(
        require_nonempty("purchase_amount", purchase_amount)
    )
    location_code = require_nonempty("location_code", location_code)
    location_name = location_name.strip() if location_name and location_name.strip() else None

    recorded_at = _now()
    try:
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT 1 FROM assets WHERE asset_code = ?", (asset_code,)
            ).fetchone()
            if existing is not None:
                raise LedgerError(
                    "duplicate-asset", f"资产编号已存在: {asset_code}"
                )
            conn.execute(
                """
                INSERT INTO assets (
                    asset_code, name, category, purchase_date,
                    purchase_amount_cents, status,
                    current_location_code, current_location_name, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset_code,
                    name,
                    category,
                    purchase_date_iso,
                    amount_cents,
                    STATUS_IN_STOCK,
                    location_code,
                    location_name,
                    recorded_at,
                ),
            )
            # Registration is the first, immutable ledger event. Its effective
            # time is the purchase date (start of day).
            conn.execute(
                """
                INSERT INTO events (
                    asset_code, type, effective_at, recorded_at,
                    from_location_code, from_location_name,
                    to_location_code, to_location_name,
                    reason, request_id
                ) VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, NULL, NULL)
                """,
                (
                    asset_code,
                    TYPE_REGISTRATION,
                    f"{purchase_date_iso}T00:00:00",
                    recorded_at,
                    location_code,
                    location_name,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    except sqlite3.Error as exc:
        raise LedgerError("db-error", f"数据库错误: {exc}") from exc

    row = conn.execute("SELECT * FROM assets WHERE asset_code = ?", (asset_code,)).fetchone()
    return {"asset": _asset_payload(row)}


def _get_asset(conn: sqlite3.Connection, asset_code: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM assets WHERE asset_code = ?", (asset_code,)
    ).fetchone()
    if row is None:
        raise LedgerError("asset-not-found", f"资产不存在: {asset_code}")
    return row


def _replay_or_conflict(
    conn: sqlite3.Connection,
    *,
    request_id: str,
    action: str,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """Return the original response for a replayed request, or raise on conflict.

    Returns ``None`` when the request_id has never been seen.
    """
    row = conn.execute(
        "SELECT payload_hash, response FROM requests WHERE request_id = ?",
        (request_id,),
    ).fetchone()
    if row is None:
        return None
    if row["payload_hash"] != _payload_hash(action, payload):
        raise LedgerError(
            "request-conflict",
            f"request_id 已用于不同载荷: {request_id}",
        )
    return json.loads(row["response"])


def _record_request(
    conn: sqlite3.Connection,
    *,
    request_id: str,
    asset_code: str,
    action: str,
    payload: dict[str, Any],
    response: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT INTO requests (request_id, asset_code, action, payload_hash, response)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            request_id,
            asset_code,
            action,
            _payload_hash(action, payload),
            json.dumps(response, ensure_ascii=False, separators=(",", ":")),
        ),
    )


def activate_asset(
    conn: sqlite3.Connection,
    *,
    asset_code: str,
    request_id: str,
    effective_at: str,
) -> dict[str, Any]:
    """Move an asset from 在库 (in_stock) to 使用中 (in_use), idempotently."""
    asset_code = require_nonempty("asset_code", asset_code)
    request_id = require_nonempty("request_id", request_id)
    effective_dt = parse_effective_at(require_nonempty("effective_at", effective_at))
    effective_iso = effective_dt.replace(microsecond=0).isoformat()
    payload = {"asset_code": asset_code, "effective_at": effective_iso}

    try:
        try:
            conn.execute("BEGIN IMMEDIATE")
            replayed = _replay_or_conflict(
                conn,
                request_id=request_id,
                action=ACTION_ACTIVATE,
                payload=payload,
            )
            if replayed is not None:
                conn.commit()
                return replayed

            row = _get_asset(conn, asset_code)
            purchase = date.fromisoformat(row["purchase_date"])
            if effective_dt.date() < purchase:
                raise LedgerError(
                    "effective-before-purchase",
                    f"生效时间 {effective_iso} 早于采购日期 {row['purchase_date']}",
                )
            if row["status"] != STATUS_IN_STOCK:
                raise LedgerError(
                    "invalid-state",
                    f"资产当前状态为 {row['status']}，仅在库资产可启用",
                )
            recorded_at = _now()
            conn.execute(
                """
                INSERT INTO events (
                    asset_code, type, effective_at, recorded_at,
                    from_location_code, from_location_name,
                    to_location_code, to_location_name,
                    reason, request_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                (
                    asset_code,
                    TYPE_ACTIVATION,
                    effective_iso,
                    recorded_at,
                    row["current_location_code"],
                    row["current_location_name"],
                    row["current_location_code"],
                    row["current_location_name"],
                    request_id,
                ),
            )
            conn.execute(
                "UPDATE assets SET status = ? WHERE asset_code = ?",
                (STATUS_IN_USE, asset_code),
            )
            new_row = conn.execute(
                "SELECT * FROM assets WHERE asset_code = ?", (asset_code,)
            ).fetchone()
            response = {"asset": _asset_payload(new_row)}
            _record_request(
                conn,
                request_id=request_id,
                asset_code=asset_code,
                action=ACTION_ACTIVATE,
                payload=payload,
                response=response,
            )
            conn.commit()
            return response
        except Exception:
            conn.rollback()
            raise
    except sqlite3.Error as exc:
        raise LedgerError("db-error", f"数据库错误: {exc}") from exc


def _refresh_current_location(conn: sqlite3.Connection, asset_code: str) -> None:
    """Recompute current location from the latest-effective move.

    Ties on effective_at are broken by submission order (later wins).  This
    makes late-arriving moves safe: the stored current position always mirrors
    the immutable history instead of blindly following the newest insert.
    """
    latest = conn.execute(
        """
        SELECT to_location_code, to_location_name
        FROM events
        WHERE asset_code = ? AND type = ?
        ORDER BY effective_at DESC, seq DESC
        LIMIT 1
        """,
        (asset_code, TYPE_MOVE),
    ).fetchone()
    if latest is None:
        # No moves yet: fall back to the registration location.
        latest = conn.execute(
            """
            SELECT to_location_code, to_location_name
            FROM events
            WHERE asset_code = ? AND type = ?
            ORDER BY seq ASC
            LIMIT 1
            """,
            (asset_code, TYPE_REGISTRATION),
        ).fetchone()
    conn.execute(
        """
        UPDATE assets
        SET current_location_code = ?, current_location_name = ?
        WHERE asset_code = ?
        """,
        (latest["to_location_code"], latest["to_location_name"], asset_code),
    )


def move_asset(
    conn: sqlite3.Connection,
    *,
    asset_code: str,
    location_code: str,
    location_name: str | None,
    reason: str | None,
    request_id: str,
    effective_at: str,
) -> dict[str, Any]:
    """Append an immutable move event and update current location atomically."""
    asset_code = require_nonempty("asset_code", asset_code)
    location_code = require_nonempty("location_code", location_code)
    request_id = require_nonempty("request_id", request_id)
    effective_dt = parse_effective_at(require_nonempty("effective_at", effective_at))
    location_name = location_name.strip() if location_name and location_name.strip() else None
    reason = reason.strip() if reason and reason.strip() else None
    effective_iso = effective_dt.replace(microsecond=0).isoformat()
    payload = {
        "asset_code": asset_code,
        "location_code": location_code,
        "location_name": location_name,
        "reason": reason,
        "effective_at": effective_iso,
    }

    try:
        try:
            conn.execute("BEGIN IMMEDIATE")
            replayed = _replay_or_conflict(
                conn,
                request_id=request_id,
                action=ACTION_MOVE,
                payload=payload,
            )
            if replayed is not None:
                conn.commit()
                return replayed

            row = _get_asset(conn, asset_code)  # fails with asset-not-found
            if row["status"] not in (STATUS_IN_STOCK, STATUS_IN_USE):
                raise LedgerError(
                    "invalid-state", f"资产当前状态 {row['status']} 不允许位置变更"
                )

            recorded_at = _now()
            conn.execute(
                """
                INSERT INTO events (
                    asset_code, type, effective_at, recorded_at,
                    from_location_code, from_location_name,
                    to_location_code, to_location_name,
                    reason, request_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset_code,
                    TYPE_MOVE,
                    effective_iso,
                    recorded_at,
                    row["current_location_code"],
                    row["current_location_name"],
                    location_code,
                    location_name,
                    reason,
                    request_id,
                ),
            )
            _refresh_current_location(conn, asset_code)
            new_row = conn.execute(
                "SELECT * FROM assets WHERE asset_code = ?", (asset_code,)
            ).fetchone()
            response = {"asset": _asset_payload(new_row)}
            _record_request(
                conn,
                request_id=request_id,
                asset_code=asset_code,
                action=ACTION_MOVE,
                payload=payload,
                response=response,
            )
            conn.commit()
            return response
        except Exception:
            conn.rollback()
            raise
    except sqlite3.Error as exc:
        raise LedgerError("db-error", f"数据库错误: {exc}") from exc


def get_asset(conn: sqlite3.Connection, asset_code: str) -> dict[str, Any]:
    asset_code = require_nonempty("asset_code", asset_code)
    row = conn.execute(
        "SELECT * FROM assets WHERE asset_code = ?", (asset_code,)
    ).fetchone()
    if row is None:
        raise LedgerError("asset-not-found", f"资产不存在: {asset_code}")
    return {"asset": _asset_payload(row)}


def get_history(conn: sqlite3.Connection, asset_code: str) -> dict[str, Any]:
    asset_code = require_nonempty("asset_code", asset_code)
    row = conn.execute(
        "SELECT 1 FROM assets WHERE asset_code = ?", (asset_code,)
    ).fetchone()
    if row is None:
        raise LedgerError("asset-not-found", f"资产不存在: {asset_code}")
    events = conn.execute(
        """
        SELECT * FROM events
        WHERE asset_code = ?
        ORDER BY effective_at ASC, seq ASC
        """,
        (asset_code,),
    ).fetchall()
    return {
        "asset_code": asset_code,
        "history": [_event_payload(event) for event in events],
    }
