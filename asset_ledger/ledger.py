"""Asset ledger storage and validation backed by SQLite."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import os
from pathlib import Path
import re
import sqlite3

DB_PATH = Path(
    os.environ.get(
        "ASSET_LEDGER_DB", Path(__file__).resolve().parents[1] / "asset_ledger.db"
    )
)

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_AMOUNT_RE = re.compile(r"\d+(\.\d{1,2})?")
_YEARS_RE = re.compile(r"[0-9]+")
_RATE_RE = re.compile(r"[0-9]+\.[0-9]{2}")

_TWO_PLACES = Decimal("0.01")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    asset_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    location TEXT NOT NULL,
    purchase_date TEXT NOT NULL,
    purchase_amount TEXT NOT NULL,
    status TEXT NOT NULL
)
"""

_MOVES_SCHEMA = """
CREATE TABLE IF NOT EXISTS asset_moves (
    asset_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    from_location TEXT NOT NULL,
    to_location TEXT NOT NULL,
    effective_date TEXT NOT NULL,
    PRIMARY KEY (asset_id, sequence)
)
"""

STATUS_IN_USE = "in_use"


class LedgerError(Exception):
    """A business-rule violation that must be reported to the user."""


@dataclass(frozen=True)
class Asset:
    asset_id: str
    name: str
    category: str
    location: str
    purchase_date: str
    purchase_amount: Decimal
    status: str
    move_count: int = 0


@dataclass(frozen=True)
class MoveRecord:
    asset_id: str
    sequence: int
    from_location: str
    to_location: str
    effective_date: str


def _require_non_empty(field: str, value: str) -> str:
    if not value or not value.strip():
        raise LedgerError(f"{field} must not be empty")
    return value


def _validate_date(value: str) -> str:
    if not _DATE_RE.fullmatch(value):
        raise LedgerError(f"purchase date must be in YYYY-MM-DD format: {value!r}")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise LedgerError(f"purchase date is not a valid date: {value!r}") from None
    return value


def _validate_amount(value: str) -> Decimal:
    if not _AMOUNT_RE.fullmatch(value):
        raise LedgerError(
            f"purchase amount must be a non-negative decimal with at most two "
            f"fraction digits: {value!r}"
        )
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise LedgerError(f"purchase amount is not a number: {value!r}") from None
    return amount.quantize(_TWO_PLACES)


def _validate_calendar_date(field: str, value: str) -> date:
    if not _DATE_RE.fullmatch(value):
        raise LedgerError(f"{field} must be in YYYY-MM-DD format: {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise LedgerError(f"{field} is not a valid date: {value!r}") from None


def _validate_useful_life_years(value: str) -> int:
    if not _YEARS_RE.fullmatch(value):
        raise LedgerError(
            f"useful life years must be a positive integer: {value!r}"
        )
    years = int(value)
    if years <= 0:
        raise LedgerError(
            f"useful life years must be a positive integer: {value!r}"
        )
    return years


def _validate_salvage_rate(value: str) -> Decimal:
    if not _RATE_RE.fullmatch(value):
        raise LedgerError(
            "salvage rate must be a decimal between 0 and 1 with exactly "
            f"two fraction digits: {value!r}"
        )
    rate = Decimal(value)
    if not Decimal("0.00") <= rate <= Decimal("1.00"):
        raise LedgerError(
            "salvage rate must be between 0 and 1 inclusive: " f"{value!r}"
        )
    return rate


def _connect(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.execute(_SCHEMA)
    connection.execute(_MOVES_SCHEMA)
    return connection


def _row_to_asset(row: tuple[object, ...]) -> Asset:
    return Asset(
        asset_id=row[0],
        name=row[1],
        category=row[2],
        location=row[3],
        purchase_date=row[4],
        purchase_amount=Decimal(row[5]),
        status=row[6],
        move_count=row[7],
    )


_ASSET_SELECT = (
    "SELECT a.asset_id, a.name, a.category, a.location, a.purchase_date, "
    "a.purchase_amount, a.status, "
    "COUNT(m.sequence) AS move_count "
    "FROM assets a "
    "LEFT JOIN asset_moves m ON m.asset_id = a.asset_id"
)


def register_asset(
    asset_id: str,
    name: str,
    category: str,
    location: str,
    purchase_date: str,
    purchase_amount: str,
    db_path: Path = DB_PATH,
) -> Asset:
    """Validate and store a new asset; raise LedgerError on any violation."""
    asset = Asset(
        asset_id=_require_non_empty("asset id", asset_id),
        name=_require_non_empty("name", name),
        category=_require_non_empty("category", category),
        location=_require_non_empty("location", location),
        purchase_date=_validate_date(purchase_date),
        purchase_amount=_validate_amount(purchase_amount),
        status=STATUS_IN_USE,
    )
    with _connect(db_path) as connection:
        try:
            connection.execute(
                "INSERT INTO assets (asset_id, name, category, location, "
                "purchase_date, purchase_amount, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    asset.asset_id,
                    asset.name,
                    asset.category,
                    asset.location,
                    asset.purchase_date,
                    str(asset.purchase_amount),
                    asset.status,
                ),
            )
        except sqlite3.IntegrityError:
            raise LedgerError(
                f"asset id already registered: {asset.asset_id!r}"
            ) from None
    return asset


def query_assets(
    category: str | None = None,
    location: str | None = None,
    db_path: Path = DB_PATH,
) -> list[Asset]:
    """Return assets matching all given filters, ordered by asset id."""
    clauses: list[str] = []
    parameters: list[str] = []
    if category is not None:
        clauses.append("a.category = ?")
        parameters.append(category)
    if location is not None:
        clauses.append("a.location = ?")
        parameters.append(location)
    statement = _ASSET_SELECT
    if clauses:
        statement += " WHERE " + " AND ".join(clauses)
    statement += " GROUP BY a.asset_id ORDER BY a.asset_id ASC"
    with _connect(db_path) as connection:
        rows = connection.execute(statement, parameters).fetchall()
    return [_row_to_asset(row) for row in rows]


def get_asset(asset_id: str, db_path: Path = DB_PATH) -> Asset | None:
    """Return the asset with the given id, or None if it does not exist."""
    with _connect(db_path) as connection:
        row = connection.execute(
            _ASSET_SELECT + " WHERE a.asset_id = ? GROUP BY a.asset_id",
            (asset_id,),
        ).fetchone()
    return _row_to_asset(row) if row is not None else None


def move_asset(
    asset_id: str,
    to_location: str,
    effective_date: str,
    db_path: Path = DB_PATH,
) -> MoveRecord:
    """Record a location change for an asset and update its current location.

    The location update and the change fact are committed atomically.
    Raises LedgerError on any validation failure or unknown asset without
    leaving partial state behind.
    """
    target = _require_non_empty("to location", to_location).strip()
    move_day = _validate_calendar_date("effective date", effective_date)

    connection = _connect(db_path)
    try:
        with connection:  # BEGIN ... COMMIT (or rollback on exception)
            row = connection.execute(
                "SELECT location, purchase_date FROM assets "
                "WHERE asset_id = ?",
                (asset_id,),
            ).fetchone()
            if row is None:
                raise LedgerError(f"asset id not found: {asset_id!r}")
            current_location = row[0]
            purchase_day = date.fromisoformat(row[1])
            if move_day < purchase_day:
                raise LedgerError(
                    f"effective date must not be earlier than purchase date "
                    f"{purchase_day.isoformat()}: {effective_date!r}"
                )
            if target == current_location:
                raise LedgerError(
                    f"to location is the same as current location: {target!r}"
                )
            next_sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM asset_moves "
                "WHERE asset_id = ?",
                (asset_id,),
            ).fetchone()[0]
            connection.execute(
                "UPDATE assets SET location = ? WHERE asset_id = ?",
                (target, asset_id),
            )
            connection.execute(
                "INSERT INTO asset_moves (asset_id, sequence, from_location, "
                "to_location, effective_date) VALUES (?, ?, ?, ?, ?)",
                (
                    asset_id,
                    next_sequence,
                    current_location,
                    target,
                    effective_date,
                ),
            )
    finally:
        connection.close()

    return MoveRecord(
        asset_id=asset_id,
        sequence=next_sequence,
        from_location=current_location,
        to_location=target,
        effective_date=effective_date,
    )


def list_moves(
    asset_id: str,
    db_path: Path = DB_PATH,
) -> list[MoveRecord]:
    """Return all location-change facts for an asset, ordered by sequence.

    Raises LedgerError if the asset is not registered.
    """
    with _connect(db_path) as connection:
        exists = connection.execute(
            "SELECT 1 FROM assets WHERE asset_id = ?", (asset_id,)
        ).fetchone()
        if exists is None:
            raise LedgerError(f"asset id not found: {asset_id!r}")
        rows = connection.execute(
            "SELECT asset_id, sequence, from_location, to_location, "
            "effective_date FROM asset_moves WHERE asset_id = ? "
            "ORDER BY sequence ASC",
            (asset_id,),
        ).fetchall()
    return [
        MoveRecord(
            asset_id=row[0],
            sequence=row[1],
            from_location=row[2],
            to_location=row[3],
            effective_date=row[4],
        )
        for row in rows
    ]


@dataclass(frozen=True)
class DepreciationResult:
    asset_id: str
    as_of: str
    purchase_amount: Decimal
    salvage_value: Decimal
    accumulated_depreciation: Decimal
    book_value: Decimal


def depreciate_asset(
    asset_id: str,
    as_of: str,
    useful_life_years: str,
    salvage_rate: str,
    db_path: Path = DB_PATH,
) -> DepreciationResult:
    """Compute straight-line depreciation for one asset up to ``as_of``.

    Read-only: raises LedgerError on any validation failure or unknown asset
    without modifying the database.
    """
    years = _validate_useful_life_years(useful_life_years)
    rate = _validate_salvage_rate(salvage_rate)
    end_date = _validate_calendar_date("as of date", as_of)

    asset = get_asset(asset_id, db_path=db_path)
    if asset is None:
        raise LedgerError(f"asset id not found: {asset_id!r}")

    purchase_amount = asset.purchase_amount
    salvage_value = (purchase_amount * rate).quantize(_TWO_PLACES, ROUND_HALF_UP)
    depreciable_base = purchase_amount - salvage_value
    total_life_days = years * 365

    purchase_day = date.fromisoformat(asset.purchase_date)
    elapsed_days = (end_date - purchase_day).days
    if elapsed_days <= 0:
        elapsed_days = 0
    elif elapsed_days > total_life_days:
        elapsed_days = total_life_days

    depreciation_cap = depreciable_base
    accumulated = (
        depreciable_base * elapsed_days / total_life_days
    ).quantize(_TWO_PLACES, ROUND_HALF_UP)
    if accumulated > depreciation_cap:
        accumulated = depreciation_cap
    book_value = purchase_amount - accumulated

    return DepreciationResult(
        asset_id=asset.asset_id,
        as_of=as_of,
        purchase_amount=purchase_amount,
        salvage_value=salvage_value,
        accumulated_depreciation=accumulated,
        book_value=book_value,
    )

