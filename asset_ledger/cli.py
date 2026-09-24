"""Command-line entry point."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__, db

DEFAULT_DB = "asset_ledger.db"


def _add_tag_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--tag", required=True, help="资产标签（台账内唯一）")


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--db",
        default=DEFAULT_DB,
        metavar="PATH",
        help=f"SQLite 数据文件路径（默认: {DEFAULT_DB}，首次登记时自动创建）",
    )
    # Sub-parser copies must not overwrite a --db parsed before the subcommand
    # with their own default: argparse otherwise resets the attribute.
    sub_common = argparse.ArgumentParser(add_help=False)
    sub_common.add_argument(
        "--db",
        default=argparse.SUPPRESS,
        metavar="PATH",
        help=f"SQLite 数据文件路径（默认: {DEFAULT_DB}，首次登记时自动创建）",
    )

    parser = argparse.ArgumentParser(
        prog="asset-ledger",
        description="本地设备资产台账：资产登记与位置变更。",
        parents=[common],
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )

    subparsers = parser.add_subparsers(dest="command")

    register = subparsers.add_parser(
        "register", parents=[sub_common], help="登记一项资产"
    )
    _add_tag_arg(register)
    register.add_argument("--name", required=True, help="资产名称")
    register.add_argument("--category", required=True, help="资产类别")
    register.add_argument(
        "--purchase-date", required=True, help="购置日期（YYYY-MM-DD）"
    )
    register.add_argument(
        "--location", required=True, help="初始存放位置"
    )
    register.add_argument(
        "--request-id",
        dest="request_id",
        default=None,
        help="请求标识，携带后可安全重试（同一标识重复提交返回同一结果）",
    )
    register.set_defaults(func=cmd_register)

    move = subparsers.add_parser(
        "move", parents=[sub_common], help="变更资产的存放位置"
    )
    _add_tag_arg(move)
    move.add_argument("--location", required=True, help="目标存放位置")
    move.add_argument("--date", required=True, help="变更日期（YYYY-MM-DD）")
    move.add_argument("--note", default="", help="变更说明（可选）")
    move.set_defaults(func=cmd_move)

    show = subparsers.add_parser(
        "show", parents=[sub_common], help="查看单个资产及其完整变更历史"
    )
    _add_tag_arg(show)
    show.set_defaults(func=cmd_show)

    list_cmd = subparsers.add_parser(
        "list", parents=[sub_common], help="列出全部资产摘要"
    )
    list_cmd.add_argument("--category", default=None, help="按类别筛选")
    list_cmd.set_defaults(func=cmd_list)

    return parser


def _open(db_path: str, *, need_write: bool):
    """Open the ledger, creating the schema for write commands.

    Read commands against a not-yet-created data file return ``None`` instead
    of creating an empty file.
    """
    path = Path(db_path)
    if not need_write and not path.exists():
        return None
    conn = db.connect(path)
    db.initialize(conn)
    return conn


def cmd_register(args: argparse.Namespace, out, err) -> int:
    # Validate before touching the filesystem so an invalid request never
    # creates an empty data file.
    tag, name, category, purchase_date, location = db.validate_registration(
        args.tag, args.name, args.category, args.purchase_date, args.location
    )
    db_path = Path(args.db)
    existed = db_path.exists()
    conn = _open(db_path, need_write=True)
    closed = False
    try:
        try:
            asset_id, tag, location = db.register_asset(
                conn,
                tag=tag,
                name=name,
                category=category,
                purchase_date=purchase_date,
                location=location,
                request_id=args.request_id,
            )
        except db.LedgerError:
            # A failed first registration must not leave an empty file behind.
            if not existed:
                conn.close()
                closed = True
                db_path.unlink(missing_ok=True)
            raise
    finally:
        if not closed:
            conn.close()
    print(f"台账编号: {asset_id}", file=out)
    print(f"资产标签: {tag}", file=out)
    print(f"初始存放位置: {location}", file=out)
    return 0


def cmd_move(args: argparse.Namespace, out, err) -> int:
    tag, location, change_date = db.validate_move(args.tag, args.location, args.date)
    conn = _open(args.db, need_write=False)
    try:
        if conn is None:
            raise db.LedgerError(f"目标资产不存在: {tag!r}")
        tag, location = db.change_location(
            conn,
            tag=tag,
            location=location,
            change_date=change_date,
            note=args.note,
        )
    finally:
        if conn is not None:
            conn.close()
    print(f"资产标签: {tag}", file=out)
    print(f"当前存放位置: {location}", file=out)
    return 0


def cmd_show(args: argparse.Namespace, out, err) -> int:
    conn = _open(args.db, need_write=False)
    try:
        asset = db.get_asset(conn, args.tag) if conn is not None else None
        if asset is None:
            print(f"错误: 未找到资产标签 {args.tag!r}", file=err)
            return 1
        print(f"资产标签: {asset['tag']}", file=out)
        print(f"名称: {asset['name']}", file=out)
        print(f"类别: {asset['category']}", file=out)
        print(f"购置日期: {asset['purchase_date']}", file=out)
        print(f"当前存放位置: {asset['current_location']}", file=out)
        history = db.get_history(conn, asset["id"])
        print("变更历史:", file=out)
        if not history:
            print("  尚无变更记录", file=out)
        else:
            for index, item in enumerate(history, start=1):
                note = item["note"] or "无"
                print(
                    f"  {index}. {item['change_date']}"
                    f" {item['from_location']} -> {item['to_location']}"
                    f"（说明: {note}）",
                    file=out,
                )
        return 0
    finally:
        if conn is not None:
            conn.close()


def cmd_list(args: argparse.Namespace, out, err) -> int:
    conn = _open(args.db, need_write=False)
    try:
        rows = db.list_assets(conn, args.category) if conn is not None else []
        for row in rows:
            print(
                f"#{row['id']} 标签:{row['tag']} 名称:{row['name']}"
                f" 类别:{row['category']} 购置日期:{row['purchase_date']}"
                f" 当前位置:{row['current_location']}",
                file=out,
            )
        return 0
    finally:
        if conn is not None:
            conn.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0

    try:
        return args.func(args, sys.stdout, sys.stderr)
    except db.LedgerError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
