"""sqlite3 本地持久化层：资产登记、位置变更、维保计划与查询。

所有写入在单个事务内完成，要么整体成功，要么整体不生效；
日期与金额均按输入字符串原样保存，不做转换或四舍五入。
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import NamedTuple

# 默认数据文件名；可通过 --db 选项或 ASSET_LEDGER_DB 环境变量覆盖。
DEFAULT_DB_FILENAME = ".asset_ledger.db"

# YYYY-MM-DD，且年月日须为真实日历日期（由 date.fromisoformat 校验）。
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 非负十进制数：整数或带小数点，可选正号，不接受负号、科学计数法与空小数。
MONEY_RE = re.compile(r"^\+?\d+(?:\.\d+)?$")

STATUS_IN_USE = "在用"
STATUS_SCRAPPED = "已报废"


class LedgerError(ValueError):
    """用户输入或业务规则错误；CLI 捕获后输出到 stderr 并以非零状态退出。"""


class AssetNotFound(LedgerError):
    """对未登记的资产编号执行操作。"""


class DuplicateAsset(LedgerError):
    """资产编号重复登记。"""


class DuplicatePlan(LedgerError):
    """维保计划编号重复登记。"""


class AssetScrapped(LedgerError):
    """对已报废资产执行仅限在用状态的操作（位置变更、维保登记等）。"""


class RequestIdConflict(LedgerError):
    """幂等请求编号已存在，但再次提交的内容与首次不一致。"""


@dataclass(frozen=True)
class LocationRecord:
    """一条位置记录：初始登记位置或一次位置变更。"""

    seq: int
    date: str
    location: str
    kind: str  # 初始 / 变更


@dataclass(frozen=True)
class AssetView:
    """查询结果：资产基本信息、当前状态、当前位置与完整位置历史。"""

    asset_id: str
    name: str
    category: str
    purchase_date: str
    cost: str
    status: str
    current_location: str
    history: tuple[LocationRecord, ...]


class RegisterResult(NamedTuple):
    asset_id: str
    status: str
    location: str


class MoveResult(NamedTuple):
    asset_id: str
    location: str


class ScrapResult(NamedTuple):
    asset_id: str
    status: str
    scrap_date: str


@dataclass(frozen=True)
class MaintenancePlan:
    """一条维保计划：按资产列出时使用。"""

    plan_id: str
    asset_id: str
    plan_type: str
    first_due_date: str
    period_days: int


@dataclass(frozen=True)
class DuePlan:
    """到期待办清单中的一条：计划信息、下一次到期日期与资产当前存放位置。"""

    plan_id: str
    asset_id: str
    plan_type: str
    next_due_date: str
    current_location: str


class PlanRegisterResult(NamedTuple):
    plan_id: str
    asset_id: str
    first_due_date: str


def _validate_required(value: str, field: str) -> str:
    if value is None or not value.strip():
        raise LedgerError(f"{field}不能为空")
    return value


def _validate_date(value: str, field: str = "日期") -> str:
    if not DATE_RE.match(value):
        raise LedgerError(f"{field}格式非法，应为 YYYY-MM-DD：{value!r}")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise LedgerError(f"{field}不是有效日历日期：{value!r}") from None
    return value


def _validate_cost(value: str) -> str:
    if not MONEY_RE.match(value):
        raise LedgerError(f"购置金额必须是以元计的非负十进制数：{value!r}")
    return value


def _validate_period_days(value: str) -> int:
    # 正整数：仅由数字组成且大于 0；拒绝 0、负数、小数与非数字内容。
    if not value or not value.isdigit() or int(value) <= 0:
        raise LedgerError(f"周期天数必须是正整数：{value!r}")
    return int(value)


def _validate_location(value: str) -> str:
    if value is None or not value.strip():
        raise LedgerError("存放位置不能为空")
    return value


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path) -> None:
    """创建表结构（已存在则不做改动）。"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS assets (
                asset_id      TEXT PRIMARY KEY,
                name          TEXT NOT NULL,
                category      TEXT NOT NULL,
                purchase_date TEXT NOT NULL,
                cost          TEXT NOT NULL,
                status        TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS location_history (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_id   TEXT NOT NULL,
                date       TEXT NOT NULL,
                chronology INTEGER NOT NULL,
                location   TEXT NOT NULL,
                kind       TEXT NOT NULL,
                FOREIGN KEY (asset_id) REFERENCES assets(asset_id)
            );

            CREATE INDEX IF NOT EXISTS idx_location_history_asset
                ON location_history(asset_id, chronology);

            CREATE TABLE IF NOT EXISTS maintenance_plans (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id         TEXT NOT NULL UNIQUE,
                asset_id        TEXT NOT NULL,
                plan_type       TEXT NOT NULL,
                first_due_date  TEXT NOT NULL,
                period_days     INTEGER NOT NULL,
                FOREIGN KEY (asset_id) REFERENCES assets(asset_id)
            );

            CREATE INDEX IF NOT EXISTS idx_maintenance_plans_asset
                ON maintenance_plans(asset_id, first_due_date, id);

            CREATE TABLE IF NOT EXISTS scrap_records (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_id   TEXT NOT NULL UNIQUE,
                scrap_date TEXT NOT NULL,
                reason     TEXT NOT NULL,
                request_id TEXT,
                FOREIGN KEY (asset_id) REFERENCES assets(asset_id)
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_scrap_records_request
                ON scrap_records(request_id) WHERE request_id IS NOT NULL;
            """
        )


def register(
    db_path: Path,
    *,
    asset_id: str,
    name: str,
    category: str,
    purchase_date: str,
    cost: str,
    location: str,
) -> RegisterResult:
    """登记新资产并写入第一条（初始）位置记录。

    校验全部通过后才写入；资产编号重复时事务回滚，
    数据库中不会留下半条记录，也不会改动既有资产。
    """
    asset_id = _validate_required(asset_id, "资产编号")
    name = _validate_required(name, "名称")
    category = _validate_required(category, "类别")
    purchase_date = _validate_date(purchase_date, "购置日期")
    cost = _validate_cost(cost)
    location = _validate_location(location)

    conn = _connect(db_path)
    try:
        with conn:  # 抛异常自动回滚，正常退出自动提交
            cursor = conn.execute(
                "INSERT INTO assets "
                "(asset_id, name, category, purchase_date, cost, status) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(asset_id) DO NOTHING",
                (asset_id, name, category, purchase_date, cost, STATUS_IN_USE),
            )
            if cursor.rowcount == 0:
                raise DuplicateAsset(f"资产编号已存在：{asset_id}")
            conn.execute(
                "INSERT INTO location_history "
                "(asset_id, date, chronology, location, kind) "
                "VALUES (?, ?, ?, ?, ?)",
                (asset_id, purchase_date, 1, location, "初始"),
            )
    finally:
        conn.close()
    return RegisterResult(asset_id, STATUS_IN_USE, location)


def move(
    db_path: Path,
    *,
    asset_id: str,
    new_location: str,
    change_date: str,
) -> MoveResult:
    """对已登记资产追加一条位置变更记录（历史只增不改）。

    变更日期不得早于该设备的购置日期，也不得早于最后一条位置记录的日期；
    资产不存在时直接拒绝，不创建资产也不写位置记录。
    同一日期允许多次变更，按录入顺序排列。
    """
    asset_id = _validate_required(asset_id, "资产编号")
    new_location = _validate_location(new_location)
    change_date = _validate_date(change_date, "变更日期")

    conn = _connect(db_path)
    try:
        with conn:
            row = conn.execute(
                "SELECT purchase_date, status FROM assets WHERE asset_id = ?",
                (asset_id,),
            ).fetchone()
            if row is None:
                raise AssetNotFound(f"资产编号未登记：{asset_id}")
            purchase_date, status = row[0], row[1]
            if status == STATUS_SCRAPPED:
                raise AssetScrapped(f"资产已报废，不能变更存放位置：{asset_id}")

            last = conn.execute(
                "SELECT date FROM location_history "
                "WHERE asset_id = ? ORDER BY chronology DESC LIMIT 1",
                (asset_id,),
            ).fetchone()
            last_date = last[0] if last else purchase_date

            if change_date < purchase_date:
                raise LedgerError(
                    f"变更日期 {change_date} 早于购置日期 {purchase_date}，拒绝变更"
                )
            if change_date < last_date:
                raise LedgerError(
                    f"变更日期 {change_date} 早于最后一条位置记录日期 {last_date}，"
                    "拒绝变更"
                )

            next_seq = conn.execute(
                "SELECT COALESCE(MAX(chronology), 0) + 1 "
                "FROM location_history WHERE asset_id = ?",
                (asset_id,),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO location_history "
                "(asset_id, date, chronology, location, kind) "
                "VALUES (?, ?, ?, ?, ?)",
                (asset_id, change_date, next_seq, new_location, "变更"),
            )
    finally:
        conn.close()
    return MoveResult(asset_id, new_location)


def scrap(
    db_path: Path,
    *,
    asset_id: str,
    scrap_date: str,
    reason: str,
    request_id: str | None = None,
) -> ScrapResult:
    """登记资产报废：状态由“在用”变为“已报废”，追加一条报废记录。

    仅“在用”资产可以报废；报废日期不得早于最后一条位置记录日期。
    报废记录只增不改，资产状态不可逆。

    幂等：提供非空 request_id 时，同一请求编号已成功报废过同一资产，
    且资产编号、报废日期、报废原因与首次完全一致，则原样返回成功结果、
    不重复追加记录；任一字段不同则报错且不写入任何数据。
    """
    asset_id = _validate_required(asset_id, "资产编号")
    scrap_date = _validate_date(scrap_date, "报废日期")
    reason = _validate_required(reason, "报废原因")
    if request_id is not None:
        request_id = _validate_required(request_id, "请求编号")

    conn = _connect(db_path)
    try:
        with conn:
            # 先按请求编号查幂等记录（同一数据文件内请求编号唯一）。
            if request_id is not None:
                existing = conn.execute(
                    "SELECT asset_id, scrap_date, reason FROM scrap_records "
                    "WHERE request_id = ?",
                    (request_id,),
                ).fetchone()
                if existing is not None:
                    if (
                        existing[0] == asset_id
                        and existing[1] == scrap_date
                        and existing[2] == reason
                    ):
                        return ScrapResult(asset_id, STATUS_SCRAPPED, scrap_date)
                    raise RequestIdConflict(
                        f"请求编号 {request_id} 已用于一次内容不同的报废登记，"
                        "拒绝重复提交"
                    )

            row = conn.execute(
                "SELECT status FROM assets WHERE asset_id = ?",
                (asset_id,),
            ).fetchone()
            if row is None:
                raise AssetNotFound(f"资产编号未登记：{asset_id}")
            status = row[0]
            if status == STATUS_SCRAPPED:
                raise AssetScrapped(f"资产已报废，不能重复报废：{asset_id}")

            last = conn.execute(
                "SELECT date FROM location_history "
                "WHERE asset_id = ? ORDER BY chronology DESC LIMIT 1",
                (asset_id,),
            ).fetchone()
            if last is not None and scrap_date < last[0]:
                raise LedgerError(
                    f"报废日期 {scrap_date} 早于最后一条位置记录日期 {last[0]}，"
                    "拒绝报废"
                )

            conn.execute(
                "UPDATE assets SET status = ? WHERE asset_id = ?",
                (STATUS_SCRAPPED, asset_id),
            )
            conn.execute(
                "INSERT INTO scrap_records (asset_id, scrap_date, reason, request_id) "
                "VALUES (?, ?, ?, ?)",
                (asset_id, scrap_date, reason, request_id),
            )
    finally:
        conn.close()
    return ScrapResult(asset_id, STATUS_SCRAPPED, scrap_date)


def get_asset(db_path: Path, *, asset_id: str) -> AssetView:
    """按资产编号返回资产基本信息、当前状态、当前位置与完整位置历史。"""
    asset_id = _validate_required(asset_id, "资产编号")
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT asset_id, name, category, purchase_date, cost, status "
            "FROM assets WHERE asset_id = ?",
            (asset_id,),
        ).fetchone()
        if row is None:
            raise AssetNotFound(f"资产编号未登记：{asset_id}")
        history_rows = conn.execute(
            "SELECT chronology, date, location, kind FROM location_history "
            "WHERE asset_id = ? ORDER BY chronology",
            (asset_id,),
        ).fetchall()
    finally:
        conn.close()

    history = tuple(
        LocationRecord(seq=r[0], date=r[1], location=r[2], kind=r[3])
        for r in history_rows
    )
    current_location = history[-1].location if history else ""
    return AssetView(
        asset_id=row[0],
        name=row[1],
        category=row[2],
        purchase_date=row[3],
        cost=row[4],
        status=row[5],
        current_location=current_location,
        history=history,
    )


def register_plan(
    db_path: Path,
    *,
    plan_id: str,
    asset_id: str,
    plan_type: str,
    first_due_date: str,
    period_days: str,
) -> PlanRegisterResult:
    """为已登记资产登记一条维保计划（历史只增不改，不提供修改或删除）。

    全部校验通过后才写入；计划编号重复或资产未登记时事务回滚，
    不会写入半条记录，也不会顺带创建资产或改动既有计划。
    """
    plan_id = _validate_required(plan_id, "计划编号")
    asset_id = _validate_required(asset_id, "资产编号")
    plan_type = _validate_required(plan_type, "维保类型")
    first_due_date = _validate_date(first_due_date, "首次到期日期")
    period = _validate_period_days(period_days)

    conn = _connect(db_path)
    try:
        with conn:
            asset_row = conn.execute(
                "SELECT status FROM assets WHERE asset_id = ?", (asset_id,)
            ).fetchone()
            if asset_row is None:
                raise AssetNotFound(f"资产编号未登记：{asset_id}")
            if asset_row[0] == STATUS_SCRAPPED:
                raise AssetScrapped(f"资产已报废，不能登记维保计划：{asset_id}")

            cursor = conn.execute(
                "INSERT INTO maintenance_plans "
                "(plan_id, asset_id, plan_type, first_due_date, period_days) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(plan_id) DO NOTHING",
                (plan_id, asset_id, plan_type, first_due_date, period),
            )
            if cursor.rowcount == 0:
                raise DuplicatePlan(f"维保计划编号已存在：{plan_id}")
    finally:
        conn.close()
    return PlanRegisterResult(plan_id, asset_id, first_due_date)


def list_plans(db_path: Path, *, asset_id: str) -> tuple[MaintenancePlan, ...]:
    """按资产编号列出其全部维保计划。

    按首次到期日期升序、同一日期按录入顺序排列；资产未登记时报错。
    """
    asset_id = _validate_required(asset_id, "资产编号")
    conn = _connect(db_path)
    try:
        asset_row = conn.execute(
            "SELECT 1 FROM assets WHERE asset_id = ?", (asset_id,)
        ).fetchone()
        if asset_row is None:
            raise AssetNotFound(f"资产编号未登记：{asset_id}")
        rows = conn.execute(
            "SELECT plan_id, asset_id, plan_type, first_due_date, period_days "
            "FROM maintenance_plans WHERE asset_id = ? "
            "ORDER BY first_due_date, id",
            (asset_id,),
        ).fetchall()
    finally:
        conn.close()
    return tuple(
        MaintenancePlan(
            plan_id=r[0],
            asset_id=r[1],
            plan_type=r[2],
            first_due_date=r[3],
            period_days=r[4],
        )
        for r in rows
    )


def _next_due_date(first_due: date, period_days: int, cutoff: date) -> date | None:
    """推算截止日（含）之前最近一次到期日期；首次到期已晚于截止日则返回 None。"""
    if first_due > cutoff:
        return None
    elapsed = (cutoff - first_due).days
    k = elapsed // period_days
    return first_due + timedelta(days=k * period_days)


def due_plans(db_path: Path, *, cutoff_date: str) -> tuple[DuePlan, ...]:
    """列出下一次到期日期早于或等于截止日期的全部计划（含恰好相等）。

    按到期日期升序、同一日期按录入顺序排列；每条附带资产当前存放位置。
    已登记但尚未到期的计划不在清单中，不属于错误。
    """
    cutoff_text = _validate_date(cutoff_date, "截止日期")
    cutoff = date.fromisoformat(cutoff_text)

    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT p.plan_id, p.asset_id, p.plan_type,
                   p.first_due_date, p.period_days,
                   (SELECT l.location FROM location_history l
                     WHERE l.asset_id = p.asset_id
                     ORDER BY l.chronology DESC LIMIT 1) AS current_location
            FROM maintenance_plans p
            ORDER BY p.id
            """
        ).fetchall()
    finally:
        conn.close()

    due: list[tuple[int, DuePlan]] = []
    for entry_order, row in enumerate(rows):
        plan_id, asset_id, plan_type, first_text, period_days, location = row
        next_due = _next_due_date(
            date.fromisoformat(first_text), period_days, cutoff
        )
        if next_due is None:
            continue
        due.append(
            (
                entry_order,
                DuePlan(
                    plan_id=plan_id,
                    asset_id=asset_id,
                    plan_type=plan_type,
                    next_due_date=next_due.isoformat(),
                    current_location=location or "",
                ),
            )
        )

    due.sort(key=lambda item: (item[1].next_due_date, item[0]))
    return tuple(item[1] for item in due)
