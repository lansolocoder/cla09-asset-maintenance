"""Command-line entry point.

Subcommands::

    register  登记新设备资产（初始状态：在库）
    activate  启用资产（在库 -> 使用中），携带 request_id 幂等
    move      位置变更，追加不可变移动历史，携带 request_id 幂等
    show      按资产编号查询当前状态（一行 JSON）
    history   查询资产事件历史（一行 JSON，按生效时间升序）

All failures go to stderr as a JSON object carrying a stable ``code`` and
exit with status 1; successful commands print one JSON object on stdout.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Sequence

from . import __version__
from . import storage


class _LedgerArgumentParser(argparse.ArgumentParser):
    """ArgumentParser that reports usage errors as stable-code JSON on stderr."""

    def error(self, message: str) -> None:
        print(
            json.dumps(
                {"error": {"code": "usage-error", "message": message}},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        self.exit(2)


def _build_parser() -> argparse.ArgumentParser:
    parser = _LedgerArgumentParser(
        prog="asset-ledger",
        description=(
            "本地设备资产台账：登记 (register)、启用 (activate)、"
            "位置变更 (move)、查询 (show/history)。"
            "数据持久化于仓库根目录 asset_ledger.sqlite3。"
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    subparsers = parser.add_subparsers(dest="command", metavar="command")

    register = subparsers.add_parser(
        "register",
        help="登记新设备资产（成功后状态为在库）",
        description="登记新设备资产，初始状态为“在库”，并写入登记事件。",
    )
    register.add_argument("--asset-code", required=True, help="资产编号（稳定唯一）")
    register.add_argument("--name", required=True, help="资产名称")
    register.add_argument("--category", required=True, help="资产类别")
    register.add_argument(
        "--purchase-date", required=True, help="采购日期（YYYY-MM-DD）"
    )
    register.add_argument(
        "--purchase-amount",
        required=True,
        help="采购金额（正数，最多两位小数，如 1299.00）",
    )
    register.add_argument(
        "--location-code", required=True, help="初始位置编码"
    )
    register.add_argument("--location-name", help="初始位置名称（可选）")

    activate = subparsers.add_parser(
        "activate",
        help="启用资产：在库 -> 使用中",
        description=(
            "启用资产，仅允许“在库”变为“使用中”；同一 request_id 同载荷重放"
            "返回原结果，异载荷返回 request-conflict。"
        ),
    )
    activate.add_argument("--asset-code", required=True, help="资产编号")
    activate.add_argument(
        "--request-id", required=True, help="本次启用的唯一请求标识（幂等键）"
    )
    activate.add_argument(
        "--effective-at",
        required=True,
        help="业务生效时间（YYYY-MM-DDTHH:MM:SS），不得早于采购日期",
    )

    move = subparsers.add_parser(
        "move",
        help="位置变更（在库/使用中均可）",
        description=(
            "登记位置变更：同事务追加不可变移动历史并更新当前位置；"
            "支持晚到记录，当前位置取生效时间最新的移动。"
            "同一 request_id 同载荷重放返回原结果，异载荷返回 request-conflict。"
        ),
    )
    move.add_argument("--asset-code", required=True, help="资产编号")
    move.add_argument(
        "--to-location-code", required=True, help="目标位置编码"
    )
    move.add_argument("--to-location-name", help="目标位置名称（可选）")
    move.add_argument("--reason", help="变更原因（可选）")
    move.add_argument(
        "--request-id", required=True, help="本次变更的唯一请求标识（幂等键）"
    )
    move.add_argument(
        "--effective-at",
        required=True,
        help="业务生效时间（YYYY-MM-DDTHH:MM:SS）",
    )

    show = subparsers.add_parser(
        "show", help="按资产编号查询资产（一行 JSON）"
    )
    show.add_argument("--asset-code", required=True, help="资产编号")

    history = subparsers.add_parser(
        "history", help="查询资产事件历史（一行 JSON，生效时间升序）"
    )
    history.add_argument("--asset-code", required=True, help="资产编号")

    return parser


def _print_json(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _fail(code: str, message: str) -> int:
    print(
        json.dumps(
            {"error": {"code": code, "message": message}},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        file=sys.stderr,
    )
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    try:
        conn = storage.connect()
        try:
            if args.command == "register":
                result = storage.register_asset(
                    conn,
                    asset_code=args.asset_code,
                    name=args.name,
                    category=args.category,
                    purchase_date=args.purchase_date,
                    purchase_amount=args.purchase_amount,
                    location_code=args.location_code,
                    location_name=args.location_name,
                )
            elif args.command == "activate":
                result = storage.activate_asset(
                    conn,
                    asset_code=args.asset_code,
                    request_id=args.request_id,
                    effective_at=args.effective_at,
                )
            elif args.command == "move":
                result = storage.move_asset(
                    conn,
                    asset_code=args.asset_code,
                    location_code=args.to_location_code,
                    location_name=args.to_location_name,
                    reason=args.reason,
                    request_id=args.request_id,
                    effective_at=args.effective_at,
                )
            elif args.command == "show":
                result = storage.get_asset(conn, args.asset_code)
            else:  # history
                result = storage.get_history(conn, args.asset_code)
        finally:
            conn.close()
    except storage.LedgerError as exc:
        return _fail(exc.code, str(exc))
    except sqlite3.Error as exc:
        return _fail("db-error", f"数据库错误: {exc}")
    except Exception as exc:  # pragma: no cover - defensive last resort
        return _fail("internal-error", str(exc))

    _print_json(result)
    return 0
