"""Command-line entry point."""

import argparse
from collections.abc import Sequence
import json
import sys

from . import __version__
from .ledger import (
    Asset,
    LedgerError,
    calculate_depreciation,
    list_due_reminders,
    query_assets,
    register_asset,
    register_maintenance_plan,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="asset-ledger",
        description="Local 设备资产与维保台账 ledger.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="command")

    register = subparsers.add_parser("register", help="登记新资产")
    register.add_argument("--asset-id", required=True, help="资产编号（业务唯一键）")
    register.add_argument("--name", required=True, help="资产名称")
    register.add_argument("--category", required=True, help="资产分类")
    register.add_argument("--location", required=True, help="存放位置")
    register.add_argument("--purchase-date", required=True, help="购置日期 YYYY-MM-DD")
    register.add_argument("--purchase-amount", required=True, help="购置金额")

    query = subparsers.add_parser("query", help="查询资产台账")
    query.add_argument("--category", help="按分类过滤")
    query.add_argument("--location", help="按存放位置过滤")

    depreciate = subparsers.add_parser(
        "depreciate", help="按直线法计算截至评估日期的累计折旧与净值"
    )
    depreciate.add_argument("--asset-id", required=True, help="资产编号（业务唯一键）")
    depreciate.add_argument("--as-of", required=True, help="评估日期 YYYY-MM-DD")
    depreciate.add_argument(
        "--annual-rate", required=True, help="年折旧率（百分数，如 5 或 2.5）"
    )

    register_plan = subparsers.add_parser(
        "register-plan", help="为已登记资产登记维保计划"
    )
    register_plan.add_argument("--asset-id", required=True, help="资产编号")
    register_plan.add_argument("--plan-id", required=True, help="计划编号（业务唯一键）")
    register_plan.add_argument("--item", required=True, help="维保项目名称")
    register_plan.add_argument("--cycle-days", required=True, help="维保周期天数（正整数）")
    register_plan.add_argument(
        "--first-due", required=True, help="首次到期日期 YYYY-MM-DD（不早于购置日期）"
    )

    reminders = subparsers.add_parser(
        "reminders", help="按到期区间列出维保到期提醒"
    )
    reminders.add_argument("--start", required=True, help="区间开始日期 YYYY-MM-DD")
    reminders.add_argument("--end", required=True, help="区间结束日期 YYYY-MM-DD")

    return parser


def _asset_to_json(asset: Asset) -> str:
    fields = {
        "asset_id": asset.asset_id,
        "name": asset.name,
        "category": asset.category,
        "location": asset.location,
        "purchase_date": asset.purchase_date,
        "status": asset.status,
    }
    parts = [f"{json.dumps(key)}: {json.dumps(value, ensure_ascii=False)}" for key, value in fields.items()]
    parts.insert(5, f'"purchase_amount": {asset.purchase_amount:.2f}')
    return "{" + ", ".join(parts) + "}"


def _run_register(args: argparse.Namespace) -> int:
    asset = register_asset(
        asset_id=args.asset_id,
        name=args.name,
        category=args.category,
        location=args.location,
        purchase_date=args.purchase_date,
        purchase_amount=args.purchase_amount,
    )
    line = {
        "asset_id": asset.asset_id,
        "name": asset.name,
        "status": asset.status,
    }
    print(json.dumps(line, ensure_ascii=False))
    return 0


def _run_query(args: argparse.Namespace) -> int:
    assets = query_assets(category=args.category, location=args.location)
    records = ", ".join(_asset_to_json(asset) for asset in assets)
    print('{"records": [' + records + "]}")
    return 0


def _run_depreciate(args: argparse.Namespace) -> int:
    result = calculate_depreciation(
        asset_id=args.asset_id,
        as_of=args.as_of,
        annual_rate=args.annual_rate,
    )
    line = (
        "{"
        f'"asset_id": {json.dumps(result["asset_id"])}, '
        f'"as_of": {json.dumps(result["as_of"])}, '
        f'"purchase_amount": {result["purchase_amount"]:.2f}, '
        f'"annual_rate": {float(result["annual_rate"])}, '
        f'"accumulated_depreciation": {result["accumulated_depreciation"]:.2f}, '
        f'"net_value": {result["net_value"]:.2f}'
        "}"
    )
    print(line)
    return 0


def _run_register_plan(args: argparse.Namespace) -> int:
    plan = register_maintenance_plan(
        plan_id=args.plan_id,
        asset_id=args.asset_id,
        item=args.item,
        cycle_days=args.cycle_days,
        first_due=args.first_due,
    )
    line = {
        "plan_id": plan.plan_id,
        "asset_id": plan.asset_id,
        "item": plan.item,
        "cycle_days": plan.cycle_days,
        "next_due": plan.first_due,
    }
    print(json.dumps(line, ensure_ascii=False))
    return 0


def _run_reminders(args: argparse.Namespace) -> int:
    records = list_due_reminders(start=args.start, end=args.end)
    print(json.dumps({"records": records}, ensure_ascii=False))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    try:
        if args.command == "register":
            return _run_register(args)
        if args.command == "depreciate":
            return _run_depreciate(args)
        if args.command == "register-plan":
            return _run_register_plan(args)
        if args.command == "reminders":
            return _run_reminders(args)
        return _run_query(args)
    except LedgerError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
