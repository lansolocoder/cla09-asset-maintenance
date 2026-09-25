"""Command-line entry point."""

import argparse
from collections.abc import Sequence
import json
import sys

from . import __version__
from .ledger import (
    Asset,
    Depreciation,
    DepreciationSummary,
    DepreciationSummaryRecord,
    LedgerError,
    RepairTicket,
    calculate_depreciation,
    calculate_depreciation_summary,
    complete_ticket,
    list_tickets,
    query_assets,
    register_asset,
    register_ticket,
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

    depreciation = subparsers.add_parser("depreciation", help="查询资产折旧")
    depreciation.add_argument("--asset-id", required=True, help="资产编号")
    depreciation.add_argument(
        "--as-of",
        help="截止日期 YYYY-MM-DD（默认系统当天）",
    )

    depreciation_summary = subparsers.add_parser(
        "depreciation-summary", help="按整月汇总全部资产折旧"
    )
    depreciation_summary.add_argument(
        "--month", required=True, help="汇总月份 YYYY-MM"
    )

    register_ticket_parser = subparsers.add_parser(
        "register-ticket", help="登记维修工单"
    )
    register_ticket_parser.add_argument(
        "--ticket-id", required=True, help="工单号（业务唯一键）"
    )
    register_ticket_parser.add_argument("--asset-id", required=True, help="资产编号")
    register_ticket_parser.add_argument(
        "--fault-description", required=True, help="故障描述"
    )
    register_ticket_parser.add_argument(
        "--submitted-date", required=True, help="送修日期 YYYY-MM-DD"
    )

    complete_ticket_parser = subparsers.add_parser(
        "complete-ticket", help="完成维修工单"
    )
    complete_ticket_parser.add_argument("--ticket-id", required=True, help="工单号")
    complete_ticket_parser.add_argument(
        "--completed-date", required=True, help="完成日期 YYYY-MM-DD"
    )

    list_tickets_parser = subparsers.add_parser(
        "list-tickets", help="按资产列出维修工单"
    )
    list_tickets_parser.add_argument("--asset-id", required=True, help="资产编号")

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


def _depreciation_to_json(depreciation: Depreciation) -> str:
    fields = {
        "asset_id": depreciation.asset_id,
        "purchase_amount": f"{depreciation.purchase_amount:.2f}",
        "residual_amount": f"{depreciation.residual_amount:.2f}",
        "as_of": depreciation.as_of,
        "elapsed_months": str(depreciation.elapsed_months),
        "monthly_depreciation": f"{depreciation.monthly_depreciation:.2f}",
        "accumulated_depreciation": f"{depreciation.accumulated_depreciation:.2f}",
        "net_book_value": f"{depreciation.net_book_value:.2f}",
    }
    numeric_keys = {
        "purchase_amount",
        "residual_amount",
        "elapsed_months",
        "monthly_depreciation",
        "accumulated_depreciation",
        "net_book_value",
    }
    parts = [
        f"{json.dumps(key)}: {value if key in numeric_keys else json.dumps(value, ensure_ascii=False)}"
        for key, value in fields.items()
    ]
    return "{" + ", ".join(parts) + "}"


def _run_depreciation(args: argparse.Namespace) -> int:
    depreciation = calculate_depreciation(
        asset_id=args.asset_id,
        as_of=args.as_of,
    )
    print(_depreciation_to_json(depreciation))
    return 0


def _summary_record_to_json(record: DepreciationSummaryRecord) -> str:
    fields = {
        "asset_id": record.asset_id,
        "purchase_amount": f"{record.purchase_amount:.2f}",
        "monthly_depreciation": f"{record.monthly_depreciation:.2f}",
        "accumulated_depreciation": f"{record.accumulated_depreciation:.2f}",
        "net_book_value": f"{record.net_book_value:.2f}",
    }
    numeric_keys = {
        "purchase_amount",
        "monthly_depreciation",
        "accumulated_depreciation",
        "net_book_value",
    }
    parts = [
        f"{json.dumps(key)}: {value if key in numeric_keys else json.dumps(value, ensure_ascii=False)}"
        for key, value in fields.items()
    ]
    return "{" + ", ".join(parts) + "}"


def _summary_to_json(summary: DepreciationSummary) -> str:
    records = ", ".join(_summary_record_to_json(r) for r in summary.records)
    totals = (
        "{"
        f'"total_monthly": {summary.total_monthly:.2f}, '
        f'"total_accumulated": {summary.total_accumulated:.2f}, '
        f'"total_net_book_value": {summary.total_net_book_value:.2f}'
        "}"
    )
    return (
        "{"
        f'"month": {json.dumps(summary.month)}, '
        f'"records": [{records}], '
        f'"totals": {totals}'
        "}"
    )


def _run_depreciation_summary(args: argparse.Namespace) -> int:
    summary = calculate_depreciation_summary(month=args.month)
    print(_summary_to_json(summary))
    return 0


def _run_register_ticket(args: argparse.Namespace) -> int:
    ticket = register_ticket(
        ticket_id=args.ticket_id,
        asset_id=args.asset_id,
        fault_description=args.fault_description,
        submitted_date=args.submitted_date,
    )
    line = {
        "asset_id": ticket.asset_id,
        "ticket_id": ticket.ticket_id,
        "status": ticket.status,
    }
    print(json.dumps(line, ensure_ascii=False))
    return 0


def _run_complete_ticket(args: argparse.Namespace) -> int:
    ticket = complete_ticket(
        ticket_id=args.ticket_id,
        completed_date=args.completed_date,
    )
    line = {
        "ticket_id": ticket.ticket_id,
        "status": ticket.status,
    }
    print(json.dumps(line, ensure_ascii=False))
    return 0


def _ticket_to_json(ticket: RepairTicket) -> str:
    fields = {
        "ticket_id": ticket.ticket_id,
        "fault_description": ticket.fault_description,
        "submitted_date": ticket.submitted_date,
        "status": ticket.status,
        "completed_date": ticket.completed_date,
    }
    parts = [
        f"{json.dumps(key)}: {json.dumps(value, ensure_ascii=False)}"
        for key, value in fields.items()
    ]
    return "{" + ", ".join(parts) + "}"


def _run_list_tickets(args: argparse.Namespace) -> int:
    tickets = list_tickets(asset_id=args.asset_id)
    records = ", ".join(_ticket_to_json(ticket) for ticket in tickets)
    print(
        '{"asset_id": '
        + json.dumps(args.asset_id, ensure_ascii=False)
        + ', "tickets": ['
        + records
        + "]}"
    )
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
        if args.command == "depreciation":
            return _run_depreciation(args)
        if args.command == "depreciation-summary":
            return _run_depreciation_summary(args)
        if args.command == "register-ticket":
            return _run_register_ticket(args)
        if args.command == "complete-ticket":
            return _run_complete_ticket(args)
        if args.command == "list-tickets":
            return _run_list_tickets(args)
        return _run_query(args)
    except LedgerError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
