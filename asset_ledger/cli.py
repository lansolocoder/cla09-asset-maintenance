"""Command-line entry point."""

import argparse
from collections.abc import Sequence
import datetime
import re
import sys

from . import __version__
from .storage import Ledger, LedgerError

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_AMOUNT_RE = re.compile(r"^(?:\d+(?:\.\d+)?|\.\d+)$")


def _non_empty(value: str) -> str:
    if not value.strip():
        raise argparse.ArgumentTypeError("该字段不能为空")
    return value


def _date(value: str) -> str:
    """校验 YYYY-MM-DD 日期，通过校验后按输入原样返回。"""
    if not _DATE_RE.match(value):
        raise argparse.ArgumentTypeError(f"日期必须是 YYYY-MM-DD 格式: {value}")
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"日期不存在: {value}") from None
    return value


def _amount(value: str) -> str:
    """校验非负十进制金额，通过校验后按输入原样返回（不四舍五入）。"""
    if not _AMOUNT_RE.match(value):
        raise argparse.ArgumentTypeError(f"金额必须是非负十进制数: {value}")
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="asset-ledger",
        description="Local 设备资产与维保台账 ledger.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    register = subparsers.add_parser("register", help="登记新资产")
    register.add_argument("--asset-id", required=True, type=_non_empty, help="资产编号（全局唯一）")
    register.add_argument("--name", required=True, type=_non_empty, help="资产名称")
    register.add_argument("--category", required=True, type=_non_empty, help="资产类别")
    register.add_argument("--purchase-date", required=True, type=_date, help="购置日期 YYYY-MM-DD")
    register.add_argument("--purchase-amount", required=True, type=_amount, help="购置金额（元，非负十进制数）")
    register.add_argument("--location", required=True, type=_non_empty, help="初始存放位置")

    relocate = subparsers.add_parser("relocate", help="变更资产存放位置")
    relocate.add_argument("--asset-id", required=True, type=_non_empty, help="资产编号")
    relocate.add_argument("--location", required=True, type=_non_empty, help="新的存放位置")
    relocate.add_argument("--change-date", required=True, type=_date, help="变更日期 YYYY-MM-DD")

    show = subparsers.add_parser("show", help="查询资产信息与位置变更历史")
    show.add_argument("--asset-id", required=True, type=_non_empty, help="资产编号")

    return parser


def _cmd_register(ledger: Ledger, args: argparse.Namespace) -> int:
    ledger.register(
        asset_id=args.asset_id,
        name=args.name,
        category=args.category,
        purchase_date=args.purchase_date,
        purchase_amount=args.purchase_amount,
        location=args.location,
    )
    print("登记成功")
    print(f"资产编号: {args.asset_id}")
    print("状态: 在用")
    print(f"存放位置: {args.location}")
    return 0


def _cmd_relocate(ledger: Ledger, args: argparse.Namespace) -> int:
    ledger.relocate(
        asset_id=args.asset_id,
        location=args.location,
        change_date=args.change_date,
    )
    print("位置变更成功")
    print(f"资产编号: {args.asset_id}")
    print(f"当前存放位置: {args.location}")
    return 0


def _cmd_show(ledger: Ledger, args: argparse.Namespace) -> int:
    asset = ledger.get_asset(args.asset_id)
    print(f"资产编号: {asset['asset_id']}")
    print(f"名称: {asset['name']}")
    print(f"类别: {asset['category']}")
    print(f"购置日期: {asset['purchase_date']}")
    print(f"购置金额: {asset['purchase_amount']} 元")
    print(f"状态: {asset['status']}")
    print(f"当前存放位置: {asset['current_location']}")
    print("位置变更历史:")
    for index, record in enumerate(asset["history"], start=1):
        print(f"  {index}. {record['change_date']} {record['location']}")
    return 0


_HANDLERS = {
    "register": _cmd_register,
    "relocate": _cmd_relocate,
    "show": _cmd_show,
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    handler = _HANDLERS.get(args.command)
    if handler is None:
        parser.print_help()
        return 0
    try:
        return handler(Ledger(), args)
    except LedgerError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
