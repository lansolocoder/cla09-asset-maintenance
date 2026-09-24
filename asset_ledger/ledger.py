"""Asset ledger storage and validation backed by SQLite."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
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

_PLAN_SCHEMA = """
CREATE TABLE IF NOT EXISTS maintenance_plans (
    asset_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    task TEXT NOT NULL,
    interval_days INTEGER NOT NULL,
    next_due TEXT NOT NULL,
    PRIMARY KEY (asset_id, plan_id),
    FOREIGN KEY (asset_id) REFERENCES assets (asset_id)
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
    asset_id: str
    plan_id: str
    task: str
    interval_days: int
    next_due: str


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


def _validate_interval_days(value: str) -> int:
    if not re.fullmatch(r"\d+", value):
        raise LedgerError(
            f"interval days must be a positive integer: {value!r}"
        )
    days = int(value)
    if days <= 0:
        raise LedgerError(
            f"interval days must be a positive integer: {value!r}"
        )
    return days


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
    connection.execute(_PLAN_SCHEMA)
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


def _row_to_plan(row: tuple[str, ...]) -> MaintenancePlan:
    return MaintenancePlan(
        asset_id=row[0],
        plan_id=row[1],
        task=row[2],
        interval_days=int(row[3]),
        next_due=row[4],
    )


def create_plan(
    asset_id: str,
    plan_id: str,
    task: str,
    interval_days: str,
    next_due: str,
    db_path: Path = DB_PATH,
) -> MaintenancePlan:
    """Validate and store a new maintenance plan; raise LedgerError on any violation."""
    plan = MaintenancePlan(
        asset_id=_require_non_empty("asset id", asset_id),
        plan_id=_require_non_empty("plan id", plan_id),
        task=_require_non_empty("task", task),
        interval_days=_validate_interval_days(interval_days),
        next_due=_validate_date(next_due, "next due date"),
    )
    with _connect(db_path) as connection:
        if connection.execute(
            "SELECT 1 FROM assets WHERE asset_id = ?", (plan.asset_id,)
        ).fetchone() is None:
            raise LedgerError(f"asset id not registered: {plan.asset_id!r}")
        try:
            connection.execute(
                "INSERT INTO maintenance_plans "
                "(asset_id, plan_id, task, interval_days, next_due) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    plan.asset_id,
                    plan.plan_id,
                    plan.task,
                    plan.interval_days,
                    plan.next_due,
                ),
            )
        except sqlite3.IntegrityError:
            raise LedgerError(
                f"plan id already registered for asset "
                f"{plan.asset_id!r}: {plan.plan_id!r}"
            ) from None
    return plan


def record_maintenance(
    asset_id: str,
    plan_id: str,
    performed_date: str,
    db_path: Path = DB_PATH,
) -> MaintenancePlan:
    """Record an execution and push the next due date forward by the interval."""
    performed = _validate_date(performed_date, "performed date")
    with _connect(db_path) as connection:
        row = connection.execute(
            "SELECT asset_id, plan_id, task, interval_days, next_due "
            "FROM maintenance_plans WHERE asset_id = ? AND plan_id = ?",
            (asset_id, plan_id),
        ).fetchone()
        if row is None:
            raise LedgerError(
                f"maintenance plan not found: {asset_id!r} / {plan_id!r}"
            )
        plan = _row_to_plan(row)
        if performed < plan.next_due:
            raise LedgerError(
                f"performed date {performed} is before next due date "
                f"{plan.next_due}"
            )
        new_next_due = (
            date.fromisoformat(performed) + timedelta(days=plan.interval_days)
        ).isoformat()
        connection.execute(
            "UPDATE maintenance_plans SET next_due = ? "
            "WHERE asset_id = ? AND plan_id = ?",
            (new_next_due, plan.asset_id, plan.plan_id),
        )
    return MaintenancePlan(
        asset_id=plan.asset_id,
        plan_id=plan.plan_id,
        task=plan.task,
        interval_days=plan.interval_days,
        next_due=new_next_due,
    )


def due_plans(
    query_date: str,
    db_path: Path = DB_PATH,
) -> list[tuple[MaintenancePlan, int]]:
    """Return plans due on or before the query date, with overdue day counts."""
    query = _validate_date(query_date, "query date")
    with _connect(db_path) as connection:
        rows = connection.execute(
            "SELECT asset_id, plan_id, task, interval_days, next_due "
            "FROM maintenance_plans WHERE next_due <= ? "
            "ORDER BY asset_id ASC, plan_id ASC",
            (query,),
        ).fetchall()
    day = date.fromisoformat(query)
    return [
        (_row_to_plan(row), (day - date.fromisoformat(row[4])).days) for row in rows
    ]
