"""SQLite persistence for the asset ledger.

数据模型：
- assets：每台设备一行，asset_id 全局唯一，登记后状态为“在用”。
- location_records：位置变更历史，只增不改；登记时的初始位置是第一条
  记录，此后每次变更追加一条，按 (change_date, id) 排序即可追溯。

所有写操作都在单个事务里完成：要么整体提交，要么整体回滚，不会留下
半条记录。
"""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3
from typing import Any

DEFAULT_DB_NAME = "asset_ledger.db"
DB_PATH_ENV_VAR = "ASSET_LEDGER_DB"

STATUS_IN_USE = "在用"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    asset_id        TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    category        TEXT NOT NULL,
    purchase_date   TEXT NOT NULL,
    purchase_amount TEXT NOT NULL,
    status          TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS location_records (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id    TEXT NOT NULL REFERENCES assets (asset_id),
    location    TEXT NOT NULL,
    change_date TEXT NOT NULL
);
"""


class LedgerError(Exception):
    """业务规则校验失败（重复编号、未登记、日期倒退等）。"""


def default_db_path() -> Path:
    """数据文件位置：环境变量 ASSET_LEDGER_DB 优先，否则为当前目录。"""
    override = os.environ.get(DB_PATH_ENV_VAR)
    if override:
        return Path(override)
    return Path.cwd() / DEFAULT_DB_NAME


class Ledger:
    def __init__(self, db_path: Path | str | None = None) -> None:
        self._db_path = Path(db_path) if db_path is not None else default_db_path()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(_SCHEMA)
        return conn

    def register(
        self,
        *,
        asset_id: str,
        name: str,
        category: str,
        purchase_date: str,
        purchase_amount: str,
        location: str,
    ) -> None:
        """登记资产并写入初始位置记录，任一插入失败则整体回滚。"""
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            try:
                conn.execute(
                    "INSERT INTO assets (asset_id, name, category, purchase_date,"
                    " purchase_amount, status) VALUES (?, ?, ?, ?, ?, ?)",
                    (asset_id, name, category, purchase_date, purchase_amount, STATUS_IN_USE),
                )
            except sqlite3.IntegrityError:
                conn.rollback()
                raise LedgerError(f"资产编号 {asset_id} 已存在，登记被拒绝") from None
            conn.execute(
                "INSERT INTO location_records (asset_id, location, change_date)"
                " VALUES (?, ?, ?)",
                (asset_id, location, purchase_date),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def relocate(self, *, asset_id: str, location: str, change_date: str) -> None:
        """追加一条位置变更记录；资产未登记或日期倒退时拒绝且不写入。"""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT purchase_date FROM assets WHERE asset_id = ?", (asset_id,)
            ).fetchone()
            if row is None:
                raise LedgerError(f"资产编号 {asset_id} 未登记，无法变更存放位置")
            (purchase_date,) = row
            if change_date < purchase_date:
                raise LedgerError(
                    f"变更日期 {change_date} 早于购置日期 {purchase_date}，变更被拒绝"
                )
            last = conn.execute(
                "SELECT change_date FROM location_records WHERE asset_id = ?"
                " ORDER BY change_date DESC, id DESC LIMIT 1",
                (asset_id,),
            ).fetchone()
            if last is not None and change_date < last[0]:
                raise LedgerError(
                    f"变更日期 {change_date} 早于上一条位置记录日期 {last[0]}，变更被拒绝"
                )
            conn.execute("BEGIN")
            conn.execute(
                "INSERT INTO location_records (asset_id, location, change_date)"
                " VALUES (?, ?, ?)",
                (asset_id, location, change_date),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_asset(self, asset_id: str) -> dict[str, Any]:
        """返回资产基本信息与完整位置历史；编号不存在时抛 LedgerError。"""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT asset_id, name, category, purchase_date, purchase_amount, status"
                " FROM assets WHERE asset_id = ?",
                (asset_id,),
            ).fetchone()
            if row is None:
                raise LedgerError(f"资产编号 {asset_id} 不存在")
            records = conn.execute(
                "SELECT change_date, location FROM location_records WHERE asset_id = ?"
                " ORDER BY change_date, id",
                (asset_id,),
            ).fetchall()
        finally:
            conn.close()
        return {
            "asset_id": row[0],
            "name": row[1],
            "category": row[2],
            "purchase_date": row[3],
            "purchase_amount": row[4],
            "status": row[5],
            "current_location": records[-1][1],
            "history": [{"change_date": d, "location": loc} for d, loc in records],
        }
