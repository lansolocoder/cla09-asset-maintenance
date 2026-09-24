"""命令行入口：资产登记、存放位置变更与查询。

调用方式：python3 -m asset_ledger <子命令> [参数]
业务错误输出到 stderr 并以非零状态退出，且不写入任何数据。
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from . import storage
from .storage import (
    DEFAULT_DB_FILENAME,
    AssetView,
    LedgerError,
    LocationRecord,
    MoveResult,
    RegisterResult,
)


def _resolve_db_path(args: argparse.Namespace) -> Path:
    db_arg = getattr(args, "db", None)
    if db_arg:
        return Path(db_arg)
    env_path = os.environ.get("ASSET_LEDGER_DB")
    if env_path:
        return Path(env_path)
    return Path.cwd() / DEFAULT_DB_FILENAME


def _print_register_result(result: RegisterResult) -> None:
    print(f"资产编号: {result.asset_id}")
    print(f"状态: {result.status}")
    print(f"初始存放位置: {result.location}")


def _print_move_result(result: MoveResult) -> None:
    print(f"资产编号: {result.asset_id}")
    print(f"当前存放位置: {result.location}")


def _print_history(history: Sequence[LocationRecord]) -> None:
    print("位置变更历史:")
    if not history:
        print("  （无记录）")
        return
    for record in history:
        print(f"  {record.seq}. {record.date} [{record.kind}] {record.location}")


def _print_asset(asset: AssetView) -> None:
    print(f"资产编号: {asset.asset_id}")
    print(f"名称: {asset.name}")
    print(f"类别: {asset.category}")
    print(f"购置日期: {asset.purchase_date}")
    print(f"购置金额(元): {asset.cost}")
    print(f"状态: {asset.status}")
    print(f"当前存放位置: {asset.current_location}")
    _print_history(asset.history)


def _add_db_argument(parser: argparse.ArgumentParser, *, suppress: bool = False) -> None:
    parser.add_argument(
        "--db",
        default=argparse.SUPPRESS if suppress else None,
        help=(
            "sqlite3 数据文件路径（默认读取环境变量 ASSET_LEDGER_DB，"
            f"否则使用当前目录下的 {DEFAULT_DB_FILENAME}）"
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="asset-ledger",
        description="本地设备资产台账：登记资产、记录存放位置变更并提供查询。",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    _add_db_argument(parser)

    subparsers = parser.add_subparsers(dest="command", metavar="命令")

    register_parser = subparsers.add_parser(
        "register", help="登记新资产并记录初始存放位置"
    )
    _add_db_argument(register_parser, suppress=True)
    register_parser.add_argument(
        "--id", dest="asset_id", required=True, help="资产编号（全局唯一）"
    )
    register_parser.add_argument("--name", required=True, help="资产名称")
    register_parser.add_argument("--category", required=True, help="资产类别")
    register_parser.add_argument(
        "--purchase-date", required=True, help="购置日期，格式 YYYY-MM-DD"
    )
    register_parser.add_argument(
        "--cost", required=True, help="购置金额（以元计的非负十进制数）"
    )
    register_parser.add_argument("--location", required=True, help="初始存放位置")
    register_parser.set_defaults(handler=_handle_register)

    move_parser = subparsers.add_parser(
        "move", help="变更已登记资产的存放位置（追加一条历史记录）"
    )
    _add_db_argument(move_parser, suppress=True)
    move_parser.add_argument("--id", dest="asset_id", required=True, help="资产编号")
    move_parser.add_argument("--location", required=True, help="新的存放位置")
    move_parser.add_argument(
        "--date", dest="change_date", required=True, help="变更日期，格式 YYYY-MM-DD"
    )
    move_parser.set_defaults(handler=_handle_move)

    show_parser = subparsers.add_parser(
        "show", help="按资产编号查询资产信息与完整位置变更历史"
    )
    _add_db_argument(show_parser, suppress=True)
    show_parser.add_argument("--id", dest="asset_id", required=True, help="资产编号")
    show_parser.set_defaults(handler=_handle_show)

    return parser


def _handle_register(args: argparse.Namespace) -> int:
    db_path = _resolve_db_path(args)
    storage.init_db(db_path)
    result = storage.register(
        db_path,
        asset_id=args.asset_id,
        name=args.name,
        category=args.category,
        purchase_date=args.purchase_date,
        cost=args.cost,
        location=args.location,
    )
    _print_register_result(result)
    return 0


def _handle_move(args: argparse.Namespace) -> int:
    db_path = _resolve_db_path(args)
    storage.init_db(db_path)
    result = storage.move(
        db_path,
        asset_id=args.asset_id,
        new_location=args.location,
        change_date=args.change_date,
    )
    _print_move_result(result)
    return 0


def _handle_show(args: argparse.Namespace) -> int:
    db_path = _resolve_db_path(args)
    storage.init_db(db_path)
    asset = storage.get_asset(db_path, asset_id=args.asset_id)
    _print_asset(asset)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    raw_args = sys.argv[1:] if argv is None else list(argv)
    # 无子命令时保持原有行为：打印帮助并以 0 退出。
    if not raw_args:
        parser.print_help()
        return 0
    args = parser.parse_args(raw_args)

    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0

    try:
        return handler(args)
    except LedgerError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
