"""Checks for the documented command-line entry point."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CommandLineTests(unittest.TestCase):
    def invoke(self, *arguments: str, db_path: str | None = None) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        if db_path is not None:
            env["ASSET_LEDGER_DB"] = db_path
        return subprocess.run(
            [sys.executable, "-m", "asset_ledger", *arguments],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

    def test_help_and_no_arguments(self) -> None:
        for arguments in [(), ("--help",)]:
            with self.subTest(arguments=arguments):
                result = self.invoke(*arguments)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("--help", result.stdout)
                self.assertIn("--version", result.stdout)
                self.assertEqual(result.stderr, "")

    def test_help_lists_business_commands(self) -> None:
        result = self.invoke("--help")
        for command in ["register", "activate", "move", "show", "history"]:
            self.assertIn(command, result.stdout)

    def test_version(self) -> None:
        result = self.invoke("--version")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "asset-ledger 0.1.0")
        self.assertEqual(result.stderr, "")

    def test_unknown_argument_is_an_error(self) -> None:
        result = self.invoke("--unknown-option")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--unknown-option", result.stderr)
        self.assertEqual(result.stdout, "")


class LedgerWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self._tmp.name) / "test.sqlite3")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def invoke(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["ASSET_LEDGER_DB"] = self.db_path
        return subprocess.run(
            [sys.executable, "-m", "asset_ledger", *arguments],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

    def json_out(self, result: subprocess.CompletedProcess[str]) -> dict:
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def json_err(self, result: subprocess.CompletedProcess[str]) -> dict:
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        return json.loads(result.stderr)

    def register(self, code: str = "A001", **overrides: str) -> dict:
        args = {
            "asset-code": code,
            "name": "笔记本电脑",
            "category": "IT设备",
            "purchase-date": "2026-01-15",
            "purchase-amount": "8999.50",
            "location-code": "WH-01",
            "location-name": "一号库房",
        }
        args.update(overrides)
        cmd: list[str] = ["register"]
        for key, value in args.items():
            cmd += [f"--{key}", value]
        return self.json_out(self.invoke(*cmd))

    def test_register_and_show(self) -> None:
        payload = self.register()
        asset = payload["asset"]
        self.assertEqual(asset["asset_code"], "A001")
        self.assertEqual(asset["status"], "in_stock")
        self.assertEqual(asset["purchase_amount"], 8999.5)
        self.assertEqual(asset["current_location"], {"code": "WH-01", "name": "一号库房"})

        shown = self.json_out(self.invoke("show", "--asset-code", "A001"))["asset"]
        self.assertEqual(shown, asset)

    def test_duplicate_asset_rejected_without_mutation(self) -> None:
        self.register()
        result = self.invoke(
            "register",
            "--asset-code", "A001",
            "--name", "另一台",
            "--category", "X",
            "--purchase-date", "2026-02-01",
            "--purchase-amount", "1.00",
            "--location-code", "WH-02",
        )
        error = self.json_err(result)
        self.assertEqual(error["error"]["code"], "duplicate-asset")

        shown = self.json_out(self.invoke("show", "--asset-code", "A001"))["asset"]
        self.assertEqual(shown["name"], "笔记本电脑")
        history = self.json_out(self.invoke("history", "--asset-code", "A001"))
        self.assertEqual(len(history["history"]), 1)

    def test_invalid_date_amount_and_missing_fields(self) -> None:
        bad_cases = [
            ["register", "--asset-code", "A002", "--name", "x", "--category", "c",
             "--purchase-date", "2026-13-40", "--purchase-amount", "1.00",
             "--location-code", "L1"],
            ["register", "--asset-code", "A002", "--name", "x", "--category", "c",
             "--purchase-date", "2026-01-01", "--purchase-amount", "-5",
             "--location-code", "L1"],
            ["register", "--asset-code", "A002", "--name", "x", "--category", "c",
             "--purchase-date", "2026-01-01", "--purchase-amount", "1.234",
             "--location-code", "L1"],
            ["register", "--asset-code", "A002", "--name", "x", "--category", "c",
             "--purchase-date", "2026-01-01", "--purchase-amount", "0",
             "--location-code", "L1"],
            ["register", "--asset-code", "  ", "--name", "x", "--category", "c",
             "--purchase-date", "2026-01-01", "--purchase-amount", "1.00",
             "--location-code", "L1"],
        ]
        for cmd in bad_cases:
            with self.subTest(cmd=cmd):
                error = self.json_err(self.invoke(*cmd))
                self.assertEqual(error["error"]["code"], "validation-error")
        self.assertEqual(
            self.json_err(self.invoke("show", "--asset-code", "A002"))["error"]["code"],
            "asset-not-found",
        )

    def test_activate_flow_and_replay(self) -> None:
        self.register()
        first = self.json_out(
            self.invoke(
                "activate", "--asset-code", "A001",
                "--request-id", "REQ-1",
                "--effective-at", "2026-02-01T09:00:00",
            )
        )
        self.assertEqual(first["asset"]["status"], "in_use")

        # Replay: same payload returns the same result, no extra event.
        replay = self.json_out(
            self.invoke(
                "activate", "--asset-code", "A001",
                "--request-id", "REQ-1",
                "--effective-at", "2026-02-01T09:00:00",
            )
        )
        self.assertEqual(replay, first)

        history = self.json_out(self.invoke("history", "--asset-code", "A001"))["history"]
        self.assertEqual([event["type"] for event in history], ["registration", "activation"])
        self.assertEqual(len(history), 2)

        # Activating again (in_use -> in_use) fails.
        error = self.json_err(
            self.invoke(
                "activate", "--asset-code", "A001",
                "--request-id", "REQ-2",
                "--effective-at", "2026-02-02T09:00:00",
            )
        )
        self.assertEqual(error["error"]["code"], "invalid-state")

        # Same request_id with a different payload -> request-conflict.
        error = self.json_err(
            self.invoke(
                "activate", "--asset-code", "A001",
                "--request-id", "REQ-1",
                "--effective-at", "2026-02-03T09:00:00",
            )
        )
        self.assertEqual(error["error"]["code"], "request-conflict")

    def test_activate_before_purchase_rejected(self) -> None:
        self.register()
        error = self.json_err(
            self.invoke(
                "activate", "--asset-code", "A001",
                "--request-id", "REQ-EARLY",
                "--effective-at", "2026-01-14T23:59:59",
            )
        )
        self.assertEqual(error["error"]["code"], "effective-before-purchase")
        shown = self.json_out(self.invoke("show", "--asset-code", "A001"))["asset"]
        self.assertEqual(shown["status"], "in_stock")

    def test_move_flow_replay_and_conflict(self) -> None:
        self.register()
        moved = self.json_out(
            self.invoke(
                "move", "--asset-code", "A001",
                "--to-location-code", "OFFICE-3", "--to-location-name", "三号办公室",
                "--reason", "领用", "--request-id", "MV-1",
                "--effective-at", "2026-02-01T10:00:00",
            )
        )
        self.assertEqual(
            moved["asset"]["current_location"],
            {"code": "OFFICE-3", "name": "三号办公室"},
        )
        self.assertEqual(moved["asset"]["status"], "in_stock")

        # Move works in 使用中 state too.
        self.json_out(
            self.invoke(
                "activate", "--asset-code", "A001",
                "--request-id", "AC-1",
                "--effective-at", "2026-02-01T09:00:00",
            )
        )
        moved2 = self.json_out(
            self.invoke(
                "move", "--asset-code", "A001",
                "--to-location-code", "OFFICE-9",
                "--request-id", "MV-2",
                "--effective-at", "2026-02-05T10:00:00",
            )
        )
        self.assertEqual(
            moved2["asset"]["current_location"],
            {"code": "OFFICE-9", "name": None},
        )

        # Replay does not append an event.
        replay = self.json_out(
            self.invoke(
                "move", "--asset-code", "A001",
                "--to-location-code", "OFFICE-9",
                "--request-id", "MV-2",
                "--effective-at", "2026-02-05T10:00:00",
            )
        )
        self.assertEqual(replay, moved2)
        history = self.json_out(self.invoke("history", "--asset-code", "A001"))["history"]
        self.assertEqual(
            [event["type"] for event in history],
            ["registration", "activation", "move", "move"],
        )

        error = self.json_err(
            self.invoke(
                "move", "--asset-code", "A001",
                "--to-location-code", "ELSEWHERE",
                "--request-id", "MV-1",
                "--effective-at", "2026-02-06T10:00:00",
            )
        )
        self.assertEqual(error["error"]["code"], "request-conflict")
        shown = self.json_out(self.invoke("show", "--asset-code", "A001"))["asset"]
        self.assertEqual(shown["current_location"]["code"], "OFFICE-9")

    def test_move_unknown_asset_fails(self) -> None:
        error = self.json_err(
            self.invoke(
                "move", "--asset-code", "NOPE",
                "--to-location-code", "X", "--request-id", "MV-X",
                "--effective-at", "2026-02-01T10:00:00",
            )
        )
        self.assertEqual(error["error"]["code"], "asset-not-found")

    def test_late_move_history_order_and_current_location(self) -> None:
        self.register()
        # Move to B effective March.
        self.json_out(
            self.invoke(
                "move", "--asset-code", "A001",
                "--to-location-code", "LOC-B",
                "--request-id", "MV-B",
                "--effective-at", "2026-03-01T10:00:00",
            )
        )
        # Late-arriving move to A effective February.
        self.json_out(
            self.invoke(
                "move", "--asset-code", "A001",
                "--to-location-code", "LOC-A",
                "--reason", "补录", "--request-id", "MV-A",
                "--effective-at", "2026-02-01T10:00:00",
            )
        )
        history = self.json_out(self.invoke("history", "--asset-code", "A001"))["history"]
        move_events = [event for event in history if event["type"] == "move"]
        self.assertEqual(
            [event["to_location"]["code"] for event in move_events],
            ["LOC-A", "LOC-B"],
        )
        # record-ed order (recorded_at) differs from effective order; later
        # submission (LOC-A) sorts earlier by effective_at and keeps its
        # recorded_at.  Check current location follows the latest effective.
        shown = self.json_out(self.invoke("show", "--asset-code", "A001"))["asset"]
        self.assertEqual(shown["current_location"]["code"], "LOC-B")

        # Same effective_at: ordered by submission sequence; current = latest.
        self.json_out(
            self.invoke(
                "move", "--asset-code", "A001",
                "--to-location-code", "LOC-C",
                "--request-id", "MV-C",
                "--effective-at", "2026-03-01T10:00:00",
            )
        )
        history = self.json_out(self.invoke("history", "--asset-code", "A001"))["history"]
        tied = [
            event for event in history
            if event["type"] == "move" and event["effective_at"] == "2026-03-01T10:00:00"
        ]
        self.assertEqual(
            [event["to_location"]["code"] for event in tied], ["LOC-B", "LOC-C"]
        )
        shown = self.json_out(self.invoke("show", "--asset-code", "A001"))["asset"]
        self.assertEqual(shown["current_location"]["code"], "LOC-C")

    def test_history_event_shape(self) -> None:
        self.register()
        self.json_out(
            self.invoke(
                "move", "--asset-code", "A001",
                "--to-location-code", "LOC-B", "--to-location-name", "B",
                "--reason", "调拨", "--request-id", "MV-1",
                "--effective-at", "2026-02-01T10:00:00",
            )
        )
        event = self.json_out(self.invoke("history", "--asset-code", "A001"))["history"][1]
        for key in (
            "effective_at", "recorded_at", "type", "from_location",
            "to_location", "reason", "request_id",
        ):
            self.assertIn(key, event)
        self.assertEqual(event["from_location"], {"code": "WH-01", "name": "一号库房"})
        self.assertEqual(event["to_location"], {"code": "LOC-B", "name": "B"})
        self.assertEqual(event["reason"], "调拨")
        self.assertEqual(event["request_id"], "MV-1")

    def test_persistence_across_invocation(self) -> None:
        self.register()
        self.json_out(
            self.invoke(
                "activate", "--asset-code", "A001",
                "--request-id", "REQ-1",
                "--effective-at", "2026-02-01T09:00:00",
            )
        )
        # A brand-new process sees committed data; replay stays idempotent.
        replay = self.json_out(
            self.invoke(
                "activate", "--asset-code", "A001",
                "--request-id", "REQ-1",
                "--effective-at", "2026-02-01T09:00:00",
            )
        )
        self.assertEqual(replay["asset"]["status"], "in_use")
        history = self.json_out(self.invoke("history", "--asset-code", "A001"))["history"]
        self.assertEqual(len(history), 2)
        self.assertTrue(Path(self.db_path).exists())

    def test_history_unknown_asset(self) -> None:
        error = self.json_err(self.invoke("history", "--asset-code", "GHOST"))
        self.assertEqual(error["error"]["code"], "asset-not-found")


if __name__ == "__main__":
    unittest.main()
