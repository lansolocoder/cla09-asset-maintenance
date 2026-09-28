"""Asset ledger storage and validation backed by SQLite."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import math
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

_MAINTENANCE_SCHEMA = """
CREATE TABLE IF NOT EXISTS maintenance_plans (
    plan_id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL,
    item TEXT NOT NULL,
    cycle_days INTEGER NOT NULL,
    first_due TEXT NOT NULL,
    UNIQUE (asset_id, item)
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


@dataclass(frozen=True)
class MaintenancePlan:
    plan_id: str
    asset_id: str
    item: str
    cycle_days: int
    first_due: str


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


def _parse_date(value: str) -> date:
    if not _DATE_RE.fullmatch(value):
        raise LedgerError(f"date must be in YYYY-MM-DD format: {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise LedgerError(f"date is not a valid date: {value!r}") from None


def _validate_rate(value: str) -> Decimal:
    try:
        rate = Decimal(value)
    except (InvalidOperation, ValueError):
        raise LedgerError(f"annual depreciation rate is not a number: {value!r}") from None
    if not rate.is_finite() or rate < 0 or rate > 100:
        raise LedgerError(
            f"annual depreciation rate must be between 0 and 100: {value!r}"
        )
    return rate


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
    connection.execute(_MAINTENANCE_SCHEMA)
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


def calculate_depreciation(
    asset_id: str,
    as_of: str,
    annual_rate: str,
    db_path: Path = DB_PATH,
) -> dict[str, object]:
    """Calculate accumulated depreciation and net value as of a date.

    Straight-line depreciation runs from the purchase date: each elapsed day
    depreciates the purchase amount by ``annual_rate / 100 / 365``. Accumulated
    depreciation is capped at the purchase amount, so net value never drops
    below zero.
    """
    _require_non_empty("asset id", asset_id)
    as_of_date = _parse_date(as_of)
    rate = _validate_rate(annual_rate)
    with _connect(db_path) as connection:
        row = connection.execute(
            "SELECT asset_id, name, category, location, purchase_date, "
            "purchase_amount, status FROM assets WHERE asset_id = ?",
            (asset_id,),
        ).fetchone()
    if row is None:
        raise LedgerError(f"unknown asset id: {asset_id!r}")
    asset = _row_to_asset(row)
    purchase_date = date.fromisoformat(asset.purchase_date)
    if as_of_date < purchase_date:
        raise LedgerError(
            f"as-of date {as_of!r} is before the purchase date "
            f"{asset.purchase_date!r}"
        )

    used_days = (as_of_date - purchase_date).days
    full_depreciation = asset.purchase_amount * (rate / Decimal(100))
    accumulated = full_depreciation * Decimal(used_days) / Decimal(365)
    cents = Decimal("0.01")
    accumulated = accumulated.quantize(cents, rounding=ROUND_HALF_UP)
    if accumulated > asset.purchase_amount:
        accumulated = asset.purchase_amount
    net_value = asset.purchase_amount - accumulated
    return {
        "asset_id": asset.asset_id,
        "as_of": as_of,
        "purchase_amount": asset.purchase_amount,
        "annual_rate": rate,
        "accumulated_depreciation": accumulated,
        "net_value": net_value,
    }


def _validate_cycle_days(value: str) -> int:
    if not re.fullmatch(r"\d+", value or ""):
        raise LedgerError(f"cycle days must be a positive integer: {value!r}")
    cycle_days = int(value)
    if cycle_days <= 0:
        raise LedgerError(f"cycle days must be a positive integer: {value!r}")
    return cycle_days


def _row_to_plan(row: tuple[str, ...]) -> MaintenancePlan:
    return MaintenancePlan(
        plan_id=row[0],
        asset_id=row[1],
        item=row[2],
        cycle_days=row[3],
        first_due=row[4],
    )


def register_maintenance_plan(
    plan_id: str,
    asset_id: str,
    item: str,
    cycle_days: str,
    first_due: str,
    db_path: Path = DB_PATH,
) -> MaintenancePlan:
    """Validate and store a maintenance plan; raise LedgerError on violation."""
    plan = MaintenancePlan(
        plan_id=_require_non_empty("plan id", plan_id),
        asset_id=_require_non_empty("asset id", asset_id),
        item=_require_non_empty("maintenance item", item),
        cycle_days=_validate_cycle_days(cycle_days),
        first_due=_validate_plan_date(first_due),
    )
    with _connect(db_path) as connection:
        asset_row = connection.execute(
            "SELECT purchase_date FROM assets WHERE asset_id = ?",
            (plan.asset_id,),
        ).fetchone()
        if asset_row is None:
            raise LedgerError(f"unknown asset id: {plan.asset_id!r}")
        if plan.first_due < asset_row[0]:
            raise LedgerError(
                f"first due date {plan.first_due!r} is before the purchase "
                f"date {asset_row[0]!r}"
            )
        if connection.execute(
            "SELECT 1 FROM maintenance_plans WHERE plan_id = ?", (plan.plan_id,)
        ).fetchone():
            raise LedgerError(f"plan id already registered: {plan.plan_id!r}")
        if connection.execute(
            "SELECT 1 FROM maintenance_plans WHERE asset_id = ? AND item = ?",
            (plan.asset_id, plan.item),
        ).fetchone():
            raise LedgerError(
                f"maintenance item already registered for asset "
                f"{plan.asset_id!r}: {plan.item!r}"
            )
        try:
            connection.execute(
                "INSERT INTO maintenance_plans (plan_id, asset_id, item, "
                "cycle_days, first_due) VALUES (?, ?, ?, ?, ?)",
                (
                    plan.plan_id,
                    plan.asset_id,
                    plan.item,
                    plan.cycle_days,
                    plan.first_due,
                ),
            )
        except sqlite3.IntegrityError:
            raise LedgerError(
                f"plan id already registered: {plan.plan_id!r}"
            ) from None
    return plan


def _validate_plan_date(value: str) -> str:
    if not _DATE_RE.fullmatch(value or ""):
        raise LedgerError(f"first due date must be in YYYY-MM-DD format: {value!r}")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise LedgerError(f"first due date is not a valid date: {value!r}") from None
    return value


def list_due_reminders(
    start: str,
    end: str,
    db_path: Path = DB_PATH,
) -> list[dict[str, object]]:
    """Return plans whose recurring due date falls in the inclusive interval.

    Each plan's due dates are its first due date plus multiples of the cycle.
    Only the earliest occurrence within ``[start, end]`` is reported per plan.
    """
    start_date = _parse_date(start)
    end_date = _parse_date(end)
    if start_date > end_date:
        raise LedgerError(
            f"start date {start!r} must not be later than end date {end!r}"
        )
    with _connect(db_path) as connection:
        rows = connection.execute(
            "SELECT plan_id, asset_id, item, cycle_days, first_due "
            "FROM maintenance_plans"
        ).fetchall()
    records: list[dict[str, object]] = []
    for row in rows:
        plan = _row_to_plan(row)
        first_date = date.fromisoformat(plan.first_due)
        if first_date > end_date:
            continue
        elapsed_days = (start_date - first_date).days
        occurrence = 0 if elapsed_days <= 0 else math.ceil(elapsed_days / plan.cycle_days)
        due_date = first_date.fromordinal(
            first_date.toordinal() + occurrence * plan.cycle_days
        )
        if due_date > end_date:
            continue
        records.append(
            {
                "plan_id": plan.plan_id,
                "asset_id": plan.asset_id,
                "item": plan.item,
                "cycle_days": plan.cycle_days,
                "due_date": due_date.isoformat(),
            }
        )
    records.sort(key=lambda record: (record["due_date"], record["plan_id"]))
    return records

