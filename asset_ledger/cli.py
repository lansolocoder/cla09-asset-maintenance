"""Command-line entry point."""

import argparse
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from . import db

DEFAULT_DB_PATH = Path("asset_ledger.db")


def _add_db_argument(parser: argparse.ArgumentParser, *, on_subparser: bool = False) -> None:
    # Subparsers use SUPPRESS so a value parsed on the root command line
    # (``asset-ledger --db X register ...``) is not clobbered by the default.
    parser.add_argument(
        "--db",
        type=Path,
        default=argparse.SUPPRESS if on_subparser else DEFAULT_DB_PATH,
        metavar="PATH",
        help="path to the SQLite ledger file (default: ./asset_ledger.db)",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="asset-ledger",
        description="Local 设备资产与维保台账 ledger.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    _add_db_argument(parser)

    subparsers = parser.add_subparsers(dest="command", metavar="command")

    register = subparsers.add_parser(
        "register", help="register a new asset"
    )
    _add_db_argument(register, on_subparser=True)
    register.add_argument("--tag", required=True, help="unique asset tag")
    register.add_argument("--name", required=True, help="asset name")
    register.add_argument("--category", required=True, help="asset category")
    register.add_argument(
        "--purchase-date",
        required=True,
        metavar="YYYY-MM-DD",
        help="purchase date",
    )
    register.add_argument(
        "--location", required=True, help="initial storage location"
    )
    register.add_argument(
        "--request-id",
        dest="request_id",
        default=None,
        help="optional idempotency key for safe retries",
    )

    move = subparsers.add_parser(
        "move", help="record a storage-location change"
    )
    _add_db_argument(move, on_subparser=True)
    move.add_argument("--tag", required=True, help="asset tag to move")
    move.add_argument(
        "--location", required=True, help="new (target) storage location"
    )
    move.add_argument(
        "--date", required=True, metavar="YYYY-MM-DD", help="change date"
    )
    move.add_argument("--note", default="", help="optional change note")

    show = subparsers.add_parser("show", help="show one asset with history")
    _add_db_argument(show, on_subparser=True)
    show.add_argument("--tag", required=True, help="asset tag")

    list_parser = subparsers.add_parser(
        "list", help="list all assets (ledger id ascending)"
    )
    _add_db_argument(list_parser, on_subparser=True)
    list_parser.add_argument(
        "--category", default=None, help="only list assets of this category"
    )

    return parser


def _connect_for_read(path: Path) -> sqlite3.Connection | None:
    """Open an existing ledger for reading; absent file means an empty ledger."""
    if not path.exists():
        return None
    return db.initialize(path)


def _cmd_register(args: argparse.Namespace, out) -> int:
    # Validate before opening/creating the file: a rejected first registration
    # must not leave an empty database behind.
    db.validate_registration(
        tag=args.tag,
        name=args.name,
        category=args.category,
        purchase_date=args.purchase_date,
        location=args.location,
    )
    conn = db.initialize(args.db)
    try:
        result = db.register_asset(
            conn,
            tag=args.tag,
            name=args.name,
            category=args.category,
            purchase_date=args.purchase_date,
            location=args.location,
            request_id=args.request_id,
        )
    finally:
        conn.close()
    print(f"Ledger ID: {result.asset_id}", file=out)
    print(f"Asset tag: {result.tag}", file=out)
    print(f"Initial location: {result.initial_location}", file=out)
    return 0


def _cmd_move(args: argparse.Namespace, out) -> int:
    db.validate_move(
        tag=args.tag,
        new_location=args.location,
        change_date=args.date,
        note=args.note,
    )
    conn = db.initialize(args.db)
    try:
        change = db.change_location(
            conn,
            tag=args.tag,
            new_location=args.location,
            change_date=args.date,
            note=args.note,
        )
    finally:
        conn.close()
    print(f"Current location: {change.new_location}", file=out)
    return 0


def _cmd_show(args: argparse.Namespace, out) -> int:
    conn = _connect_for_read(args.db)
    try:
        if conn is None:
            raise db.LedgerError(f"asset tag {args.tag!r} not found")
        asset, history = db.get_asset(conn, args.tag)
    finally:
        if conn is not None:
            conn.close()

    print(f"Asset tag: {asset.tag}", file=out)
    print(f"Name: {asset.name}", file=out)
    print(f"Category: {asset.category}", file=out)
    print(f"Purchase date: {asset.purchase_date}", file=out)
    print(f"Current location: {asset.current_location}", file=out)
    print("Location history:", file=out)
    if not history:
        print("  (no location changes recorded)", file=out)
    else:
        for change in history:
            line = (
                f"  {change.change_date}: {change.old_location} -> "
                f"{change.new_location}"
            )
            if change.note:
                line += f" | note: {change.note}"
            print(line, file=out)
    return 0


def _cmd_list(args: argparse.Namespace, out) -> int:
    conn = _connect_for_read(args.db)
    try:
        if conn is None:
            assets = []
        else:
            assets = db.list_assets(conn, category=args.category)
    finally:
        if conn is not None:
            conn.close()

    for asset in assets:
        print(
            f"[{asset.id}] {asset.tag} | {asset.name} | {asset.category} | "
            f"{asset.purchase_date} | {asset.current_location}",
            file=out,
        )
    return 0


_COMMANDS = {
    "register": _cmd_register,
    "move": _cmd_move,
    "show": _cmd_show,
    "list": _cmd_list,
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    # argparse exits the process on parse errors (code 2, message on stderr),
    # matching the existing nonzero-exit-on-unknown-argument behaviour.
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    handler = _COMMANDS[args.command]
    try:
        return handler(args, sys.stdout)
    except db.LedgerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
