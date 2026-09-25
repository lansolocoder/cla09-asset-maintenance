"""Asset ledger storage and validation backed by SQLite."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
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

_SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    asset_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    location TEXT NOT NULL,
    purchase_date TEXT NOT NULL,
    purchase_amount TEXT NOT NULL,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS repair_tickets (
    ticket_id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL,
    fault_description TEXT NOT NULL,
    submitted_date TEXT NOT NULL,
    status TEXT NOT NULL,
    completed_date TEXT
)
"""

STATUS_IN_USE = "in_use"

TICKET_STATUS_SUBMITTED = "submitted"
TICKET_STATUS_COMPLETED = "completed"

DEPRECIATION_YEARS = 5
RESIDUAL_RATE = Decimal("0.05")
_TWO_PLACES = Decimal("0.01")


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


def _validate_date_field(field: str, value: str) -> str:
    if not _DATE_RE.fullmatch(value):
        raise LedgerError(f"{field} must be in YYYY-MM-DD format: {value!r}")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise LedgerError(f"{field} is not a valid date: {value!r}") from None
    return value


def _validate_date(value: str) -> str:
    return _validate_date_field("purchase date", value)


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
    connection.executescript(_SCHEMA)
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


def get_asset(asset_id: str, db_path: Path = DB_PATH) -> Asset:
    """Return the asset with the given id; raise LedgerError if it does not exist."""
    statement = (
        "SELECT asset_id, name, category, location, purchase_date, "
        "purchase_amount, status FROM assets WHERE asset_id = ?"
    )
    with _connect(db_path) as connection:
        row = connection.execute(statement, (asset_id,)).fetchone()
    if row is None:
        raise LedgerError(f"asset not found: {asset_id!r}")
    return _row_to_asset(row)


@dataclass(frozen=True)
class RepairTicket:
    ticket_id: str
    asset_id: str
    fault_description: str
    submitted_date: str
    status: str
    completed_date: str | None


def _row_to_ticket(row: tuple[str, ...]) -> RepairTicket:
    return RepairTicket(
        ticket_id=row[0],
        asset_id=row[1],
        fault_description=row[2],
        submitted_date=row[3],
        status=row[4],
        completed_date=row[5],
    )


_TICKET_COLUMNS = (
    "ticket_id, asset_id, fault_description, submitted_date, status, completed_date"
)


def register_ticket(
    ticket_id: str,
    asset_id: str,
    fault_description: str,
    submitted_date: str,
    db_path: Path = DB_PATH,
) -> RepairTicket:
    """Validate and store a new repair ticket; raise LedgerError on any violation.

    The submitted date must not be earlier than the asset's purchase date;
    a date later than today is stored as given.
    """
    ticket = RepairTicket(
        ticket_id=_require_non_empty("ticket id", ticket_id),
        asset_id=_require_non_empty("asset id", asset_id),
        fault_description=_require_non_empty("fault description", fault_description),
        submitted_date=_validate_date_field("submitted date", submitted_date),
        status=TICKET_STATUS_SUBMITTED,
        completed_date=None,
    )
    with _connect(db_path) as connection:
        row = connection.execute(
            "SELECT purchase_date FROM assets WHERE asset_id = ?",
            (ticket.asset_id,),
        ).fetchone()
        if row is None:
            raise LedgerError(f"asset not found: {ticket.asset_id!r}")
        if date.fromisoformat(ticket.submitted_date) < date.fromisoformat(row[0]):
            raise LedgerError(
                f"submitted date must not be earlier than the asset's purchase "
                f"date {row[0]}: {ticket.submitted_date!r}"
            )
        try:
            connection.execute(
                "INSERT INTO repair_tickets (ticket_id, asset_id, "
                "fault_description, submitted_date, status, completed_date) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    ticket.ticket_id,
                    ticket.asset_id,
                    ticket.fault_description,
                    ticket.submitted_date,
                    ticket.status,
                    ticket.completed_date,
                ),
            )
        except sqlite3.IntegrityError:
            raise LedgerError(
                f"ticket id already registered: {ticket.ticket_id!r}"
            ) from None
    return ticket


def complete_ticket(
    ticket_id: str,
    completed_date: str,
    db_path: Path = DB_PATH,
) -> RepairTicket:
    """Mark a submitted ticket as completed; raise LedgerError on any violation."""
    completed_date = _validate_date_field("completed date", completed_date)
    with _connect(db_path) as connection:
        row = connection.execute(
            f"SELECT {_TICKET_COLUMNS} FROM repair_tickets WHERE ticket_id = ?",
            (ticket_id,),
        ).fetchone()
        if row is None:
            raise LedgerError(f"ticket not found: {ticket_id!r}")
        ticket = _row_to_ticket(row)
        if ticket.status == TICKET_STATUS_COMPLETED:
            raise LedgerError(f"ticket already completed: {ticket_id!r}")
        if date.fromisoformat(completed_date) < date.fromisoformat(
            ticket.submitted_date
        ):
            raise LedgerError(
                f"completed date must not be earlier than the submitted date "
                f"{ticket.submitted_date}: {completed_date!r}"
            )
        connection.execute(
            "UPDATE repair_tickets SET status = ?, completed_date = ? "
            "WHERE ticket_id = ?",
            (TICKET_STATUS_COMPLETED, completed_date, ticket_id),
        )
    return RepairTicket(
        ticket_id=ticket.ticket_id,
        asset_id=ticket.asset_id,
        fault_description=ticket.fault_description,
        submitted_date=ticket.submitted_date,
        status=TICKET_STATUS_COMPLETED,
        completed_date=completed_date,
    )


def list_tickets(asset_id: str, db_path: Path = DB_PATH) -> list[RepairTicket]:
    """Return the tickets of one asset ordered by ticket id.

    Read-only; raises LedgerError if the asset does not exist.
    """
    get_asset(asset_id, db_path=db_path)
    with _connect(db_path) as connection:
        rows = connection.execute(
            f"SELECT {_TICKET_COLUMNS} FROM repair_tickets WHERE asset_id = ? "
            "ORDER BY ticket_id ASC",
            (asset_id,),
        ).fetchall()
    return [_row_to_ticket(row) for row in rows]


def _validate_as_of_date(value: str) -> date:
    if not _DATE_RE.fullmatch(value):
        raise LedgerError(f"as-of date must be in YYYY-MM-DD format: {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise LedgerError(f"as-of date is not a valid date: {value!r}") from None


_MONTH_RE = re.compile(r"(\d{4})-(\d{2})")


def _validate_month(value: str) -> tuple[int, int]:
    match = _MONTH_RE.fullmatch(value or "")
    if not match:
        raise LedgerError(f"month must be in YYYY-MM format: {value!r}")
    year, month = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12:
        raise LedgerError(f"month is not a valid month: {value!r}")
    return year, month


def _month_end(year: int, month: int) -> date:
    """Last day of the given month (the 1st of next month minus one day)."""
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    return next_month - timedelta(days=1)


def _elapsed_whole_months(start: date, end: date) -> int:
    """Whole months between two dates; an unfinished month is not counted."""
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return max(months, 0)


@dataclass(frozen=True)
class Depreciation:
    asset_id: str
    purchase_amount: Decimal
    residual_amount: Decimal
    as_of: str
    elapsed_months: int
    monthly_depreciation: Decimal
    accumulated_depreciation: Decimal
    net_book_value: Decimal


def _depreciation_schedule(
    asset: Asset,
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """Return (residual, depreciable total, monthly rate, purchase amount)."""
    purchase_amount = asset.purchase_amount
    residual_amount = (purchase_amount * RESIDUAL_RATE).quantize(
        _TWO_PLACES, rounding=ROUND_HALF_UP
    )
    depreciable_amount = purchase_amount - residual_amount
    annual_depreciation = depreciable_amount / Decimal(DEPRECIATION_YEARS)
    monthly_depreciation = (annual_depreciation / 12).quantize(
        _TWO_PLACES, rounding=ROUND_HALF_UP
    )
    return residual_amount, depreciable_amount, monthly_depreciation, purchase_amount


def _accumulated_balance(
    monthly_depreciation: Decimal,
    depreciable_amount: Decimal,
    elapsed_months: int,
) -> Decimal:
    """Accumulated depreciation after the given number of whole months.

    Never below zero or above the total depreciable amount.
    """
    accumulated = monthly_depreciation * elapsed_months
    accumulated = min(max(accumulated, Decimal("0.00")), depreciable_amount)
    return accumulated.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


def calculate_depreciation(
    asset_id: str,
    as_of: str | None = None,
    db_path: Path = DB_PATH,
) -> Depreciation:
    """Compute straight-line depreciation for one asset as of a date.

    Read-only: the ledger is never modified.
    """
    asset = get_asset(asset_id, db_path=db_path)
    if as_of is None:
        as_of_date = date.today()
        as_of = as_of_date.isoformat()
    else:
        as_of_date = _validate_as_of_date(as_of)

    residual_amount, depreciable_amount, monthly_depreciation, purchase_amount = (
        _depreciation_schedule(asset)
    )

    elapsed_months = _elapsed_whole_months(
        date.fromisoformat(asset.purchase_date), as_of_date
    )
    accumulated = _accumulated_balance(
        monthly_depreciation, depreciable_amount, elapsed_months
    )
    net_book_value = (purchase_amount - accumulated).quantize(
        _TWO_PLACES, rounding=ROUND_HALF_UP
    )

    return Depreciation(
        asset_id=asset.asset_id,
        purchase_amount=purchase_amount,
        residual_amount=residual_amount,
        as_of=as_of,
        elapsed_months=elapsed_months,
        monthly_depreciation=monthly_depreciation,
        accumulated_depreciation=accumulated,
        net_book_value=net_book_value,
    )


@dataclass(frozen=True)
class DepreciationSummaryRecord:
    asset_id: str
    purchase_amount: Decimal
    monthly_depreciation: Decimal
    accumulated_depreciation: Decimal
    net_book_value: Decimal


@dataclass(frozen=True)
class DepreciationSummary:
    month: str
    records: list[DepreciationSummaryRecord]
    total_monthly: Decimal
    total_accumulated: Decimal
    total_net_book_value: Decimal


def calculate_depreciation_summary(
    month: str,
    db_path: Path = DB_PATH,
) -> DepreciationSummary:
    """Compute straight-line depreciation for every asset for one month.

    The month's last day is the cut-off for accumulated depreciation and net
    book value. Read-only: the ledger is never modified.
    """
    year, month_number = _validate_month(month)
    month_end_date = _month_end(year, month_number)
    previous_month_end = date(year, month_number, 1) - timedelta(days=1)

    records: list[DepreciationSummaryRecord] = []
    total_monthly = Decimal("0.00")
    total_accumulated = Decimal("0.00")
    total_net_book_value = Decimal("0.00")

    for asset in query_assets(db_path=db_path):
        residual_amount, depreciable_amount, monthly_rate, purchase_amount = (
            _depreciation_schedule(asset)
        )
        purchase_date = date.fromisoformat(asset.purchase_date)

        elapsed_at_end = _elapsed_whole_months(purchase_date, month_end_date)
        elapsed_at_start = _elapsed_whole_months(purchase_date, previous_month_end)
        opening_accumulated = _accumulated_balance(
            monthly_rate, depreciable_amount, elapsed_at_start
        )
        accumulated = _accumulated_balance(
            monthly_rate, depreciable_amount, elapsed_at_end
        )
        net_book_value = (purchase_amount - accumulated).quantize(
            _TWO_PLACES, rounding=ROUND_HALF_UP
        )

        # The month is charged only when another whole depreciation month has
        # elapsed by its end (months before purchase, and the purchase month
        # itself, carry no charge). The final charge is capped by the
        # depreciable amount still open at the start of the month, so a full
        # period is never charged twice and every month afterwards is 0.00.
        if month_end_date < purchase_date or elapsed_at_end <= elapsed_at_start:
            current_month_charge = Decimal("0.00")
        else:
            current_month_charge = min(
                monthly_rate, depreciable_amount - opening_accumulated
            )
            current_month_charge = max(current_month_charge, Decimal("0.00")).quantize(
                _TWO_PLACES, rounding=ROUND_HALF_UP
            )

        records.append(
            DepreciationSummaryRecord(
                asset_id=asset.asset_id,
                purchase_amount=purchase_amount,
                monthly_depreciation=current_month_charge,
                accumulated_depreciation=accumulated,
                net_book_value=net_book_value,
            )
        )
        total_monthly += current_month_charge
        total_accumulated += accumulated
        total_net_book_value += net_book_value

    return DepreciationSummary(
        month=month,
        records=records,
        total_monthly=total_monthly.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP),
        total_accumulated=total_accumulated.quantize(
            _TWO_PLACES, rounding=ROUND_HALF_UP
        ),
        total_net_book_value=total_net_book_value.quantize(
            _TWO_PLACES, rounding=ROUND_HALF_UP
        ),
    )
