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
    get_asset,
    query_assets,
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

    depreciate = subparsers.add_parser("depreciate", help="直线法折旧查询")
    depreciate.add_argument("--asset-id", help="资产编号")
    depreciate.add_argument("--as-of", help="截止日期 YYYY-MM-DD")
    depreciate.add_argument(
        "--useful-life-years", help="使用年限（正整数）"
    )
    depreciate.add_argument(
        "--salvage-rate", help="残值率（0 到 1 之间两位小数）"
    )

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
    missing = [
        name
        for name, value in [
            ("--asset-id", args.asset_id),
            ("--as-of", args.as_of),
            ("--useful-life-years", args.useful_life_years),
            ("--salvage-rate", args.salvage_rate),
        ]
        if value is None
    ]
    if missing:
        raise LedgerError(f"missing required arguments: {' '.join(missing)}")
    asset = get_asset(args.asset_id)
    result = calculate_depreciation(
        asset,
        as_of=args.as_of,
        useful_life_years=args.useful_life_years,
        salvage_rate=args.salvage_rate,
    )
    line = (
        "{"
        f'"asset_id": {json.dumps(result["asset_id"])}, '
        f'"as_of": {json.dumps(result["as_of"])}, '
        f'"purchase_amount": {result["purchase_amount"]:.2f}, '
        f'"salvage_value": {result["salvage_value"]:.2f}, '
        f'"accumulated_depreciation": {result["accumulated_depreciation"]:.2f}, '
        f'"book_value": {result["book_value"]:.2f}'
        "}"
    )
    print(line)
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
        return _run_query(args)
    except LedgerError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
