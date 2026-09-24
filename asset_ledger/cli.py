"""Command-line entry point."""

import argparse
from collections.abc import Sequence
import json
import sys

from . import __version__
from .ledger import (
    Asset,
    LedgerError,
    create_plan,
    query_assets,
    query_due_plans,
    record_maintenance,
    register_asset,
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

    create_plan = subparsers.add_parser("create-plan", help="创建维保计划")
    create_plan.add_argument("--asset-id", required=True, help="资产编号")
    create_plan.add_argument("--plan-id", required=True, help="计划编号（同一资产下唯一）")
    create_plan.add_argument("--task", required=True, help="维护内容")
    create_plan.add_argument("--interval-days", required=True, help="周期天数（正整数）")
    create_plan.add_argument("--next-due", required=True, help="下次执行日期 YYYY-MM-DD")

    record = subparsers.add_parser("record-maintenance", help="记录维保执行")
    record.add_argument("--asset-id", required=True, help="资产编号")
    record.add_argument("--plan-id", required=True, help="计划编号")
    record.add_argument("--execution-date", required=True, help="执行日期 YYYY-MM-DD")

    due = subparsers.add_parser("due", help="查询到期维保计划")
    due.add_argument("query_date", metavar="DATE", help="查询日期 YYYY-MM-DD")

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


def _run_create_plan(args: argparse.Namespace) -> int:
    plan = create_plan(
        asset_id=args.asset_id,
        plan_id=args.plan_id,
        task=args.task,
        interval_days=args.interval_days,
        next_due=args.next_due,
    )
    line = {
        "asset_id": plan.asset_id,
        "plan_id": plan.plan_id,
        "task": plan.task,
        "interval_days": plan.interval_days,
        "next_due": plan.next_due,
    }
    print(json.dumps(line, ensure_ascii=False))
    return 0


def _run_record_maintenance(args: argparse.Namespace) -> int:
    plan = record_maintenance(
        asset_id=args.asset_id,
        plan_id=args.plan_id,
        execution_date=args.execution_date,
    )
    line = {
        "asset_id": plan.asset_id,
        "plan_id": plan.plan_id,
        "next_due": plan.next_due,
    }
    print(json.dumps(line, ensure_ascii=False))
    return 0


def _run_due(args: argparse.Namespace) -> int:
    due_plans = query_due_plans(query_date=args.query_date)
    records = [
        {
            "asset_id": plan.asset_id,
            "plan_id": plan.plan_id,
            "task": plan.task,
            "interval_days": plan.interval_days,
            "next_due": plan.next_due,
            "overdue_days": overdue_days,
        }
        for plan, overdue_days in due_plans
    ]
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
        if args.command == "query":
            return _run_query(args)
        if args.command == "create-plan":
            return _run_create_plan(args)
        if args.command == "record-maintenance":
            return _run_record_maintenance(args)
        return _run_due(args)
    except LedgerError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
