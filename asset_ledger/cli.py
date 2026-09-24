"""Command-line entry point."""

import argparse
import json
import re
import sqlite3
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from . import __version__

DB_FILENAME = "asset_ledger.db"
STATUS_IN_USE = "in_use"

_AMOUNT_RE = re.compile(r"^\d+(?:\.\d{1,2})?$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _db_path() -> Path:
    # Persist in the repository root regardless of the current working directory.
    return Path(__file__).resolve().parent.parent / DB_FILENAME


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="asset-ledger",
        description="Local 设备资产与维保台账 ledger.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command")

    register = subparsers.add_parser(
        "register",
        aliases=["add", "create"],
        help="登记一项资产（资产编号唯一，重复登记将被拒绝）。",
    )
    register.add_argument("--asset-id", required=True, help="资产编号（业务唯一键）")
    register.add_argument("--name", required=True, help="资产名称")
    register.add_argument("--category", required=True, help="资产分类")
    register.add_argument("--location", required=True, help="存放位置")
    register.add_argument(
        "--purchase-date", required=True, help="购置日期，格式 YYYY-MM-DD"
    )
    register.add_argument(
        "--purchase-amount",
        required=True,
        help="购置金额，两位小数以内的非负数（如 1200 或 1200.50）",
    )

    query = subparsers.add_parser(
        "list", aliases=["query", "ls"], help="查询资产台账，输出 JSON。"
    )
    query.add_argument("--category", help="按分类过滤")
    query.add_argument("--location", help="按存放位置过滤")

    return parser


def _validate_register(args: argparse.Namespace) -> tuple[dict, str | None]:
    fields = {
        "asset_id": (args.asset_id, "资产编号"),
        "name": (args.name, "资产名称"),
        "category": (args.category, "资产分类"),
        "location": (args.location, "存放位置"),
    }
    cleaned: dict = {}
    for key, (value, label) in fields.items():
        value = value.strip()
        if not value:
            return {}, f"{label}不能为空"
        cleaned[key] = value

    date_text = args.purchase_date.strip()
    if not _DATE_RE.fullmatch(date_text):
        return {}, "购置日期必须为 YYYY-MM-DD 格式"
    try:
        datetime.strptime(date_text, "%Y-%m-%d")
    except ValueError:
        return {}, "购置日期必须为有效的日历日期（YYYY-MM-DD）"
    cleaned["purchase_date"] = date_text

    amount_text = args.purchase_amount.strip()
    if not _AMOUNT_RE.fullmatch(amount_text):
        return {}, "购置金额必须为两位小数以内的非负数"
    whole, _, fraction = amount_text.partition(".")
    amount_cents = int(whole) * 100 + int(fraction.ljust(2, "0")) if fraction else int(whole) * 100
    cleaned["amount_cents"] = amount_cents

    return cleaned, None


def _init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS assets (
            asset_id       TEXT PRIMARY KEY,
            name           TEXT NOT NULL,
            category       TEXT NOT NULL,
            location       TEXT NOT NULL,
            purchase_date  TEXT NOT NULL,
            amount_cents   INTEGER NOT NULL,
            status         TEXT NOT NULL
        )
        """
    )


def _do_register(asset: dict) -> int:
    with sqlite3.connect(_db_path()) as conn:
        _init_db(conn)
        try:
            conn.execute(
                """
                INSERT INTO assets (
                    asset_id, name, category, location,
                    purchase_date, amount_cents, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset["asset_id"],
                    asset["name"],
                    asset["category"],
                    asset["location"],
                    asset["purchase_date"],
                    asset["amount_cents"],
                    STATUS_IN_USE,
                ),
            )
        except sqlite3.IntegrityError:
            print(
                f"资产编号 {asset['asset_id']!r} 已存在，重复登记被拒绝",
                file=sys.stderr,
            )
            return 1
    print(
        f"asset_id={asset['asset_id']} name={asset['name']} status={STATUS_IN_USE}"
    )
    return 0


def _render_json_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _do_list(category: str | None, location: str | None) -> int:
    clauses = []
    params: list[str] = []
    if category is not None:
        clauses.append("category = ?")
        params.append(category)
    if location is not None:
        clauses.append("location = ?")
        params.append(location)
    sql = (
        "SELECT asset_id, name, category, location, purchase_date, amount_cents, status"
        " FROM assets"
    )
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY asset_id ASC"

    with sqlite3.connect(_db_path()) as conn:
        _init_db(conn)
        rows = conn.execute(sql, params).fetchall()

    record_parts = []
    for asset_id, name, cat, loc, purchase_date, amount_cents, status in rows:
        amount_text = f"{amount_cents // 100}.{amount_cents % 100:02d}"
        record_parts.append(
            "{"
            f'"asset_id": {_render_json_string(asset_id)}, '
            f'"name": {_render_json_string(name)}, '
            f'"category": {_render_json_string(cat)}, '
            f'"location": {_render_json_string(loc)}, '
            f'"purchase_date": {_render_json_string(purchase_date)}, '
            f'"purchase_amount": {amount_text}, '
            f'"status": {_render_json_string(status)}'
            "}"
        )
    if record_parts:
        output = '{"records": [\n  ' + ",\n  ".join(record_parts) + "\n]}\n"
    else:
        output = '{"records": []}\n'
    sys.stdout.write(output)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    if args.command in ("register", "add", "create"):
        asset, error = _validate_register(args)
        if error is not None:
            print(f"登记失败：{error}", file=sys.stderr)
            return 2
        return _do_register(asset)

    if args.command in ("list", "query", "ls"):
        return _do_list(args.category, args.location)

    parser.error(f"未知命令：{args.command}")
    return 2  # pragma: no cover - parser.error raises SystemExit
