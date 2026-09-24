"""SQLite-backed persistence for the asset ledger.

The data file is created lazily on the first registration: read operations
(show/list) against a database file that does not exist yet simply report an
empty ledger instead of creating the file.
"""

from __future__ import annotations

import datetime as _dt
import sqlite3
from dataclasses import dataclass
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assets (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    tag              TEXT NOT NULL UNIQUE,
    name             TEXT NOT NULL,
    category         TEXT NOT NULL,
    purchase_date    TEXT NOT NULL,
    current_location TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS registrations (
    request_id       TEXT PRIMARY KEY,
    asset_id         INTEGER NOT NULL,
    tag              TEXT NOT NULL,
    name             TEXT NOT NULL,
    category         TEXT NOT NULL,
    purchase_date    TEXT NOT NULL,
    initial_location TEXT NOT NULL,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (asset_id) REFERENCES assets (id)
);

CREATE TABLE IF NOT EXISTS location_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id     INTEGER NOT NULL,
    change_date  TEXT NOT NULL,
    old_location TEXT NOT NULL,
    new_location TEXT NOT NULL,
    note         TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (asset_id) REFERENCES assets (id)
);

CREATE INDEX IF NOT EXISTS idx_history_asset ON location_history (asset_id, id);
CREATE INDEX IF NOT EXISTS idx_assets_category ON assets (category);
"""


class LedgerError(Exception):
    """A user-facing ledger failure (duplicate tag, bad date, ...)."""


@dataclass(frozen=True)
class Asset:
    id: int
    tag: str
    name: str
    category: str
    purchase_date: str
    current_location: str


@dataclass(frozen=True)
class LocationChange:
    change_date: str
    old_location: str
    new_location: str
    note: str


@dataclass(frozen=True)
class RegistrationResult:
    asset_id: int
    tag: str
    initial_location: str
    replayed: bool


def validate_date(raw: str) -> str:
    """Validate a YYYY-MM-DD date, rejecting loosely formatted input."""
    if not isinstance(raw, str) or len(raw) != 10 or raw[4] != "-" or raw[7] != "-":
        raise LedgerError(f"invalid date {raw!r}: expected YYYY-MM-DD")
    year_s, month_s, day_s = raw.split("-")
    if not (year_s.isdigit() and month_s.isdigit() and day_s.isdigit()):
        raise LedgerError(f"invalid date {raw!r}: expected YYYY-MM-DD")
    try:
        _dt.date(int(year_s), int(month_s), int(day_s))
    except ValueError as exc:
        raise LedgerError(f"invalid date {raw!r}: expected YYYY-MM-DD") from exc
    return raw


def initialize(path: Path) -> sqlite3.Connection:
    """Open the database at *path* (creating it) and ensure the schema."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT INTO schema_meta (key, value) VALUES ('version', ?) "
            "ON CONFLICT(key) DO NOTHING",
            (str(SCHEMA_VERSION),),
        )
        conn.commit()
    except BaseException:
        conn.close()
        raise
    return conn


def _require(value: str | None, field: str) -> str:
    if value is None or not value.strip():
        raise LedgerError(f"{field} is required")
    return value.strip() if field == "tag" else value


def validate_registration(
    *,
    tag: str | None,
    name: str | None,
    category: str | None,
    purchase_date: str | None,
    location: str | None,
) -> tuple[str, str, str, str, str]:
    """Validate registration fields without touching the database."""
    tag = _require(tag, "asset tag")
    name = _require(name, "asset name")
    category = _require(category, "asset category")
    location = _require(location, "initial location")
    purchase_date = validate_date(_require(purchase_date, "purchase date"))
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
) -> RegistrationResult:
    """Register one asset; idempotent when *request_id* is supplied.

    All validation happens before any write, so a rejected attempt leaves no
    partial asset and cannot poison a later retry with the same request id.
    """
    tag, name, category, purchase_date, location = validate_registration(
        tag=tag,
        name=name,
        category=category,
        purchase_date=purchase_date,
        location=location,
    )

    # Replay a previously accepted request before any uniqueness check: the
    # retry must return the original result even if the tag now collides with
    # an asset registered by a different request.
    if request_id is not None:
        prior = conn.execute(
            "SELECT asset_id, tag, initial_location, name, category, purchase_date "
            "FROM registrations WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        if prior is not None:
            (pid, ptag, plocation, pname, pcategory, pdate) = prior
            if (ptag, pname, pcategory, pdate, plocation) != (
                tag,
                name,
                category,
                purchase_date,
                location,
            ):
                raise LedgerError(
                    f"request id {request_id!r} was already submitted with "
                    "different registration data"
                )
            return RegistrationResult(pid, ptag, plocation, replayed=True)

    try:
        conn.execute("BEGIN IMMEDIATE")
        if request_id is not None:
            # Re-check under the write lock to resolve concurrent retries.
            prior = conn.execute(
                "SELECT asset_id, tag, initial_location, name, category, purchase_date "
                "FROM registrations WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if prior is not None:
                (pid, ptag, plocation, pname, pcategory, pdate) = prior
                if (ptag, pname, pcategory, pdate, plocation) != (
                    tag,
                    name,
                    category,
                    purchase_date,
                    location,
                ):
                    raise LedgerError(
                        f"request id {request_id!r} was already submitted with "
                        "different registration data"
                    )
                conn.commit()
                return RegistrationResult(pid, ptag, plocation, replayed=True)

        duplicate = conn.execute(
            "SELECT 1 FROM assets WHERE tag = ?", (tag,)
        ).fetchone()
        if duplicate is not None:
            raise LedgerError(f"asset tag {tag!r} already exists")

        cur = conn.execute(
            "INSERT INTO assets (tag, name, category, purchase_date, current_location) "
            "VALUES (?, ?, ?, ?, ?)",
            (tag, name, category, purchase_date, location),
        )
        asset_id = cur.lastrowid
        if request_id is not None:
            conn.execute(
                "INSERT INTO registrations "
                "(request_id, asset_id, tag, name, category, purchase_date, initial_location) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (request_id, asset_id, tag, name, category, purchase_date, location),
            )
        conn.commit()
    except LedgerError:
        conn.rollback()
        raise
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        # Lost a race against a concurrent writer; re-check to report precisely.
        if request_id is not None:
            prior = conn.execute(
                "SELECT asset_id, tag, initial_location, name, category, purchase_date "
                "FROM registrations WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if prior is not None:
                (pid, ptag, plocation, pname, pcategory, pdate) = prior
                if (ptag, pname, pcategory, pdate, plocation) == (
                    tag,
                    name,
                    category,
                    purchase_date,
                    location,
                ):
                    return RegistrationResult(pid, ptag, plocation, replayed=True)
        raise LedgerError(f"asset tag {tag!r} already exists") from exc

    return RegistrationResult(asset_id, tag, location, replayed=False)


def validate_move(
    *,
    tag: str | None,
    new_location: str | None,
    change_date: str | None,
    note: str | None = None,
) -> tuple[str, str, str, str]:
    """Validate move-command fields without touching the database."""
    tag = _require(tag, "asset tag")
    new_location = _require(new_location, "target location")
    change_date = validate_date(_require(change_date, "change date"))
    return tag, new_location, change_date, note or ""


def change_location(
    conn: sqlite3.Connection,
    *,
    tag: str | None,
    new_location: str | None,
    change_date: str | None,
    note: str | None = None,
) -> LocationChange:
    """Move an asset, appending an immutable history entry."""
    tag, new_location, change_date, note = validate_move(
        tag=tag,
        new_location=new_location,
        change_date=change_date,
        note=note,
    )

    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT id, current_location FROM assets WHERE tag = ?", (tag,)
        ).fetchone()
        if row is None:
            raise LedgerError(f"asset tag {tag!r} not found")
        asset_id, current_location = row

        if new_location == current_location:
            raise LedgerError(
                f"target location is the same as the current location {current_location!r}"
            )

        latest = conn.execute(
            "SELECT change_date FROM location_history WHERE asset_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (asset_id,),
        ).fetchone()
        if latest is not None and change_date < latest[0]:
            raise LedgerError(
                f"change date {change_date} is earlier than the latest change "
                f"date {latest[0]}"
            )

        conn.execute(
            "INSERT INTO location_history "
            "(asset_id, change_date, old_location, new_location, note) "
            "VALUES (?, ?, ?, ?, ?)",
            (asset_id, change_date, current_location, new_location, note),
        )
        conn.execute(
            "UPDATE assets SET current_location = ? WHERE id = ?",
            (new_location, asset_id),
        )
        conn.commit()
    except LedgerError:
        conn.rollback()
        raise
    except BaseException:
        conn.rollback()
        raise

    return LocationChange(change_date, current_location, new_location, note)


def get_asset(conn: sqlite3.Connection, tag: str) -> tuple[Asset, list[LocationChange]]:
    row = conn.execute(
        "SELECT id, tag, name, category, purchase_date, current_location "
        "FROM assets WHERE tag = ?",
        (tag,),
    ).fetchone()
    if row is None:
        raise LedgerError(f"asset tag {tag!r} not found")
    asset = Asset(*row)
    history = [
        LocationChange(cdate, old, new, note)
        for cdate, old, new, note in conn.execute(
            "SELECT change_date, old_location, new_location, note "
            "FROM location_history WHERE asset_id = ? ORDER BY id",
            (asset.id,),
        )
    ]
    return asset, history


def list_assets(
    conn: sqlite3.Connection, category: str | None = None
) -> list[Asset]:
    if category is not None:
        rows = conn.execute(
            "SELECT id, tag, name, category, purchase_date, current_location "
            "FROM assets WHERE category = ? ORDER BY id",
            (category,),
        )
    else:
        rows = conn.execute(
            "SELECT id, tag, name, category, purchase_date, current_location "
            "FROM assets ORDER BY id"
        )
    return [Asset(*row) for row in rows]
