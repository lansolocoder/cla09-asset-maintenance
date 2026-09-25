"""命令行入口：资产登记、存放位置变更、维保计划与查询。

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
    DueItem,
    LedgerError,
    LocationRecord,
    MaintenancePlan,
    MoveResult,
    PlanResult,
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


def _print_plan_result(result: PlanResult) -> None:
    print(f"计划编号: {result.plan_id}")
    print(f"资产编号: {result.asset_id}")
    print(f"首次到期日期: {result.first_due}")


def _print_plans(asset_id: str, plans: Sequence[MaintenancePlan]) -> None:
    print(f"资产 {asset_id} 的维保计划:")
    if not plans:
        print("  （无记录）")
        return
    for seq, plan in enumerate(plans, start=1):
        print(
            f"  {seq}. 计划编号: {plan.plan_id} "
            f"维保类型: {plan.maint_type} "
            f"首次到期日期: {plan.first_due} "
            f"周期天数: {plan.interval_days}"
        )


def _print_due_items(until: str, items: Sequence[DueItem]) -> None:
    print(f"到期待办（截止日期 {until}）:")
    if not items:
        print("  （无到期计划）")
        return
    for seq, item in enumerate(items, start=1):
        print(
            f"  {seq}. 计划编号: {item.plan_id} "
            f"资产编号: {item.asset_id} "
            f"维保类型: {item.maint_type} "
            f"下一次到期日期: {item.next_due} "
            f"当前存放位置: {item.location}"
        )


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
        description="本地设备资产台账：登记资产、记录存放位置变更、管理维保计划并提供查询。",
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

    plan_parser = subparsers.add_parser(
        "plan", help="为已登记资产登记维保计划（历史计划只增不改）"
    )
    _add_db_argument(plan_parser, suppress=True)
    plan_parser.add_argument(
        "--id", dest="plan_id", required=True, help="计划编号（全局唯一）"
    )
    plan_parser.add_argument("--asset", dest="asset_id", required=True, help="资产编号")
    plan_parser.add_argument("--type", dest="maint_type", required=True, help="维保类型")
    plan_parser.add_argument(
        "--first-due", required=True, help="首次到期日期，格式 YYYY-MM-DD"
    )
    plan_parser.add_argument(
        "--interval", required=True, help="周期天数（正整数）"
    )
    plan_parser.set_defaults(handler=_handle_plan)

    plans_parser = subparsers.add_parser(
        "plans", help="按资产编号列出该资产的全部维保计划"
    )
    _add_db_argument(plans_parser, suppress=True)
    plans_parser.add_argument("--asset", dest="asset_id", required=True, help="资产编号")
    plans_parser.set_defaults(handler=_handle_plans)

    due_parser = subparsers.add_parser(
        "due", help="到期待办：列出下一次到期日期不晚于截止日期的维保计划"
    )
    _add_db_argument(due_parser, suppress=True)
    due_parser.add_argument(
        "--until", required=True, help="截止日期，格式 YYYY-MM-DD"
    )
    due_parser.set_defaults(handler=_handle_due)

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


def _handle_plan(args: argparse.Namespace) -> int:
    db_path = _resolve_db_path(args)
    storage.init_db(db_path)
    result = storage.register_plan(
        db_path,
        plan_id=args.plan_id,
        asset_id=args.asset_id,
        maint_type=args.maint_type,
        first_due=args.first_due,
        interval=args.interval,
    )
    _print_plan_result(result)
    return 0


def _handle_plans(args: argparse.Namespace) -> int:
    db_path = _resolve_db_path(args)
    storage.init_db(db_path)
    plans = storage.list_plans(db_path, asset_id=args.asset_id)
    _print_plans(args.asset_id, plans)
    return 0


def _handle_due(args: argparse.Namespace) -> int:
    db_path = _resolve_db_path(args)
    storage.init_db(db_path)
    items = storage.due_plans(db_path, until=args.until)
    _print_due_items(args.until, items)
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
