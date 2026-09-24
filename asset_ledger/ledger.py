"""Asset ledger storage and validation backed by SQLite."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
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

STATUS_IN_USE = "in_use"

RESIDUAL_RATE = Decimal("0.05")
DEPRECIATION_YEARS = 5
_MONTHS_PER_YEAR = 12
_CENT = Decimal("0.01")


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


def _require_non_empty(field: str, value: str) -> str:
    if not value or not value.strip():
        raise LedgerError(f"{field} must not be empty")
    return value


def _validate_date(value: str, field: str = "purchase date") -> str:
    if not _DATE_RE.fullmatch(value):
        raise LedgerError(f"{field} must be in YYYY-MM-DD format: {value!r}")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise LedgerError(f"{field} is not a valid date: {value!r}") from None
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
    return amount.quantize(Decimal("0.01"))


def _connect(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.execute(_SCHEMA)
    return connection


def _row_to_asset(row: tuple[str, ...]) -> Asset:
    return Asset(
        asset_id=row[0],
        name=row[1],
        category=row[2],
        location=row[3],
        purchase_date=row[4],
        purchase_amount=Decimal(row[5]),
        status=row[6],
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
        clauses.append("category = ?")
        parameters.append(category)
    if location is not None:
        clauses.append("location = ?")
        parameters.append(location)
    statement = (
        "SELECT asset_id, name, category, location, purchase_date, "
        "purchase_amount, status FROM assets"
    )
    if clauses:
        statement += " WHERE " + " AND ".join(clauses)
    statement += " ORDER BY asset_id ASC"
    with _connect(db_path) as connection:
        rows = connection.execute(statement, parameters).fetchall()
    return [_row_to_asset(row) for row in rows]


@dataclass(frozen=True)
class DepreciationReport:
    asset_id: str
    purchase_amount: Decimal
    residual_amount: Decimal
    as_of: str
    elapsed_months: int
    monthly_depreciation: Decimal
    accumulated_depreciation: Decimal
    net_book_value: Decimal


def find_asset(asset_id: str, db_path: Path = DB_PATH) -> Asset | None:
    """Return the asset with the given id, or None if it is not registered."""
    with _connect(db_path) as connection:
        row = connection.execute(
            "SELECT asset_id, name, category, location, purchase_date, "
            "purchase_amount, status FROM assets WHERE asset_id = ?",
            (asset_id,),
        ).fetchone()
    return _row_to_asset(row) if row is not None else None


def _round_cents(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def _elapsed_months(purchase: date, as_of: date) -> int:
    """Whole months between two dates; a partial month does not count."""
    if as_of < purchase:
        return 0
    months = (as_of.year - purchase.year) * 12 + (as_of.month - purchase.month)
    if as_of.day < purchase.day:
        months -= 1
    return months


def compute_depreciation(
    asset_id: str,
    as_of: str | None = None,
    db_path: Path = DB_PATH,
) -> DepreciationReport:
    """Straight-line depreciation report; read-only, raises LedgerError on bad input."""
    if as_of is None:
        as_of_date = date.today()
        as_of = as_of_date.isoformat()
    else:
        _validate_date(as_of, field="as-of date")
        as_of_date = date.fromisoformat(as_of)
    asset = find_asset(asset_id, db_path)
    if asset is None:
        raise LedgerError(f"asset id not found: {asset_id!r}")
    purchase_amount = asset.purchase_amount
    residual_amount = _round_cents(purchase_amount * RESIDUAL_RATE)
    monthly_depreciation = _round_cents(
        (purchase_amount - residual_amount) / (DEPRECIATION_YEARS * _MONTHS_PER_YEAR)
    )
    elapsed_months = _elapsed_months(date.fromisoformat(asset.purchase_date), as_of_date)
    accumulated_depreciation = _round_cents(monthly_depreciation * elapsed_months)
    accumulated_depreciation = min(accumulated_depreciation, purchase_amount - residual_amount)
    net_book_value = purchase_amount - accumulated_depreciation
    return DepreciationReport(
        asset_id=asset.asset_id,
        purchase_amount=purchase_amount,
        residual_amount=residual_amount,
        as_of=as_of,
        elapsed_months=elapsed_months,
        monthly_depreciation=monthly_depreciation,
        accumulated_depreciation=accumulated_depreciation,
        net_book_value=net_book_value,
    )
