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

# 正整数：可选正号，不接受负号、小数点与非数字。
POSITIVE_INT_RE = re.compile(r"^\+?\d+$")

STATUS_IN_USE = "在用"


class LedgerError(ValueError):
    """用户输入或业务规则错误；CLI 捕获后输出到 stderr 并以非零状态退出。"""


class AssetNotFound(LedgerError):
    """对未登记的资产编号执行操作。"""


class DuplicateAsset(LedgerError):
    """资产编号重复登记。"""


class DuplicatePlan(LedgerError):
    """维保计划编号重复登记。"""


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


@dataclass(frozen=True)
class MaintenancePlan:
    """一条维保计划：业务主键为计划编号，历史只增不改。"""

    plan_id: str
    asset_id: str
    maint_type: str
    first_due: str
    interval_days: int


class PlanResult(NamedTuple):
    plan_id: str
    asset_id: str
    first_due: str


class DueItem(NamedTuple):
    plan_id: str
    asset_id: str
    maint_type: str
    next_due: str
    location: str


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


def _validate_location(value: str) -> str:
    if value is None or not value.strip():
        raise LedgerError("存放位置不能为空")
    return value


def _validate_interval(value: str) -> int:
    if not POSITIVE_INT_RE.match(value):
        raise LedgerError(f"周期天数必须是正整数：{value!r}")
    days = int(value)
    if days <= 0:
        raise LedgerError(f"周期天数必须是正整数：{value!r}")
    return days


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
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id       TEXT NOT NULL UNIQUE,
                asset_id      TEXT NOT NULL,
                maint_type    TEXT NOT NULL,
                first_due     TEXT NOT NULL,
                interval_days INTEGER NOT NULL,
                FOREIGN KEY (asset_id) REFERENCES assets(asset_id)
            );

            CREATE INDEX IF NOT EXISTS idx_maintenance_plans_asset
                ON maintenance_plans(asset_id);
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
                "SELECT purchase_date FROM assets WHERE asset_id = ?",
                (asset_id,),
            ).fetchone()
            if row is None:
                raise AssetNotFound(f"资产编号未登记：{asset_id}")
            purchase_date = row[0]

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
    maint_type: str,
    first_due: str,
    interval: str,
) -> PlanResult:
    """为已登记资产登记一条维保计划（历史计划只增不改）。

    校验全部通过且资产已登记后才写入；计划编号重复时事务回滚，
    不会覆盖或改动先前登记的那条计划，也不会留下半条记录。
    """
    plan_id = _validate_required(plan_id, "计划编号")
    asset_id = _validate_required(asset_id, "资产编号")
    maint_type = _validate_required(maint_type, "维保类型")
    first_due = _validate_date(first_due, "首次到期日期")
    interval_days = _validate_interval(interval)

    conn = _connect(db_path)
    try:
        with conn:  # 抛异常自动回滚，正常退出自动提交
            row = conn.execute(
                "SELECT 1 FROM assets WHERE asset_id = ?",
                (asset_id,),
            ).fetchone()
            if row is None:
                raise AssetNotFound(f"资产编号未登记：{asset_id}")
            cursor = conn.execute(
                "INSERT INTO maintenance_plans "
                "(plan_id, asset_id, maint_type, first_due, interval_days) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(plan_id) DO NOTHING",
                (plan_id, asset_id, maint_type, first_due, interval_days),
            )
            if cursor.rowcount == 0:
                raise DuplicatePlan(f"计划编号已存在：{plan_id}")
    finally:
        conn.close()
    return PlanResult(plan_id, asset_id, first_due)


def list_plans(db_path: Path, *, asset_id: str) -> tuple[MaintenancePlan, ...]:
    """按资产编号返回该资产的全部维保计划，按首次到期日期与录入顺序排列。"""
    asset_id = _validate_required(asset_id, "资产编号")
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT 1 FROM assets WHERE asset_id = ?",
            (asset_id,),
        ).fetchone()
        if row is None:
            raise AssetNotFound(f"资产编号未登记：{asset_id}")
        rows = conn.execute(
            "SELECT plan_id, asset_id, maint_type, first_due, interval_days "
            "FROM maintenance_plans WHERE asset_id = ? "
            "ORDER BY first_due, id",
            (asset_id,),
        ).fetchall()
    finally:
        conn.close()
    return tuple(
        MaintenancePlan(
            plan_id=r[0],
            asset_id=r[1],
            maint_type=r[2],
            first_due=r[3],
            interval_days=r[4],
        )
        for r in rows
    )


def _current_location(conn: sqlite3.Connection, asset_id: str) -> str:
    row = conn.execute(
        "SELECT location FROM location_history "
        "WHERE asset_id = ? ORDER BY chronology DESC LIMIT 1",
        (asset_id,),
    ).fetchone()
    return row[0] if row else ""


def due_plans(db_path: Path, *, until: str) -> tuple[DueItem, ...]:
    """到期待办：下一次到期日期早于或等于截止日期的全部计划。

    下一次到期日期取满足 首次到期日期 + k×周期天数 ≤ 截止日期（k 为非负整数）
    的最近一次日期；首次到期日期晚于截止日期的计划不进入清单。
    结果按到期日期升序、同一日期按录入顺序排列。
    """
    until = _validate_date(until, "截止日期")
    cutoff = date.fromisoformat(until)

    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, plan_id, asset_id, maint_type, first_due, interval_days "
            "FROM maintenance_plans ORDER BY id",
        ).fetchall()
        locations = {
            asset_id: _current_location(conn, asset_id)
            for (asset_id,) in conn.execute(
                "SELECT DISTINCT asset_id FROM maintenance_plans"
            ).fetchall()
        }
    finally:
        conn.close()

    items: list[tuple[date, int, DueItem]] = []
    for seq, plan_id, asset_id, maint_type, first_due, interval_days in rows:
        first = date.fromisoformat(first_due)
        if first > cutoff:
            continue  # 尚未到期，不属于错误，只是不进入清单
        k = (cutoff - first).days // interval_days
        next_due = first + timedelta(days=k * interval_days)
        items.append(
            (
                next_due,
                seq,
                DueItem(
                    plan_id=plan_id,
                    asset_id=asset_id,
                    maint_type=maint_type,
                    next_due=next_due.isoformat(),
                    location=locations.get(asset_id, ""),
                ),
            )
        )
    items.sort(key=lambda entry: (entry[0], entry[1]))
    return tuple(item for _, _, item in items)
