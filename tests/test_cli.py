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
    def invoke(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "asset_ledger", *arguments],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_help_and_no_arguments(self) -> None:
        for arguments in [(), ("--help",)]:
            with self.subTest(arguments=arguments):
                result = self.invoke(*arguments)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("--help", result.stdout)
                self.assertIn("--version", result.stdout)
                self.assertEqual(result.stderr, "")

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


class LedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.db_path = Path(self._tmpdir.name) / "asset_ledger.db"

    def invoke(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ, ASSET_LEDGER_DB=str(self.db_path))
        return subprocess.run(
            [sys.executable, "-m", "asset_ledger", *arguments],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def register(self, asset_id: str, **overrides: str) -> subprocess.CompletedProcess[str]:
        fields = {
            "asset-id": asset_id,
            "name": "示波器",
            "category": "仪器",
            "location": "实验室B",
            "purchase-date": "2026-09-25",
            "purchase-amount": "123.40",
        }
        fields.update(overrides)
        arguments = ["register"]
        for key, value in fields.items():
            arguments += [f"--{key}", value]
        return self.invoke(*arguments)

    def query_records(self, *arguments: str) -> list[dict[str, object]]:
        result = self.invoke("query", *arguments)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)["records"]

    def test_register_and_query_round_trip(self) -> None:
        result = self.register("A002")
        self.assertEqual(result.returncode, 0, result.stderr)
        line = json.loads(result.stdout)
        self.assertEqual(line, {"asset_id": "A002", "name": "示波器", "status": "in_use"})

        self.register("A001", name="电脑", category="办公设备", **{"purchase-amount": "8999"})
        records = self.query_records()
        self.assertEqual([r["asset_id"] for r in records], ["A001", "A002"])
        self.assertEqual(records[0]["status"], "in_use")
        self.assertEqual(records[0]["purchase_date"], "2026-09-25")
        self.assertIsInstance(records[0]["purchase_amount"], float)
        self.assertEqual(records[0]["purchase_amount"], 8999.00)
        self.assertEqual(records[1]["purchase_amount"], 123.40)
        self.assertIn('"purchase_amount": 8999.00', self.invoke("query").stdout)

    def test_query_filters_intersect(self) -> None:
        self.register("A001", location="实验室A")
        self.register("A002", category="办公设备")
        self.register("A003")
        self.assertEqual(
            [r["asset_id"] for r in self.query_records("--category", "仪器")],
            ["A001", "A003"],
        )
        self.assertEqual(
            [
                r["asset_id"]
                for r in self.query_records("--category", "仪器", "--location", "实验室A")
            ],
            ["A001"],
        )
        self.assertEqual(self.query_records("--category", "不存在"), [])

    def test_duplicate_asset_id_is_rejected(self) -> None:
        self.assertEqual(self.register("A001").returncode, 0)
        result = self.register("A001", name="另一台")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotEqual(result.stderr, "")
        self.assertEqual(result.stdout, "")
        records = self.query_records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["name"], "示波器")

    def test_invalid_input_is_rejected_without_changes(self) -> None:
        bad_calls = [
            {"asset_id": "", },
            {"name": ""},
            {"category": ""},
            {"location": ""},
            {"purchase-date": "2026/09/25"},
            {"purchase-date": "2026-13-01"},
            {"purchase-amount": "-1"},
            {"purchase-amount": "1.234"},
            {"purchase-amount": "abc"},
        ]
        for overrides in bad_calls:
            with self.subTest(overrides=overrides):
                translated = {
                    {"asset_id": "asset-id"}.get(k, k).replace("_", "-"): v
                    for k, v in overrides.items()
                }
                result = self.register("A001", **translated)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotEqual(result.stderr, "")
                self.assertEqual(result.stdout, "")
        self.assertEqual(self.query_records(), [])

    def test_missing_required_argument_is_an_error(self) -> None:
        result = self.invoke("register", "--asset-id", "A001", "--name", "x")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotEqual(result.stderr, "")
        self.assertEqual(result.stdout, "")
        self.assertEqual(self.query_records(), [])


class MaintenancePlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.db_path = Path(self._tmpdir.name) / "asset_ledger.db"
        result = self.invoke(
            "register",
            "--asset-id", "A001",
            "--name", "电脑",
            "--category", "办公设备",
            "--location", "办公室",
            "--purchase-date", "2026-09-25",
            "--purchase-amount", "8999.00",
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def invoke(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ, ASSET_LEDGER_DB=str(self.db_path))
        return subprocess.run(
            [sys.executable, "-m", "asset_ledger", *arguments],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def plan(self, asset_id: str = "A001", plan_id: str = "P001", **overrides: str) -> subprocess.CompletedProcess[str]:
        fields = {
            "asset-id": asset_id,
            "plan-id": plan_id,
            "task": "更换滤网",
            "interval-days": "90",
            "next-due": "2026-10-01",
        }
        fields.update(overrides)
        arguments = ["plan"]
        for key, value in fields.items():
            arguments += [f"--{key}", value]
        return self.invoke(*arguments)

    def perform(self, asset_id: str = "A001", plan_id: str = "P001", performed_date: str = "2026-10-01") -> subprocess.CompletedProcess[str]:
        return self.invoke(
            "perform",
            "--asset-id", asset_id,
            "--plan-id", plan_id,
            "--performed-date", performed_date,
        )

    def due_records(self, query_date: str) -> list[dict[str, object]]:
        result = self.invoke("due", "--date", query_date)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)["records"]

    def test_plan_create_and_due_round_trip(self) -> None:
        result = self.plan()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "asset_id": "A001",
                "plan_id": "P001",
                "task": "更换滤网",
                "interval_days": 90,
                "next_due": "2026-10-01",
            },
        )
        self.assertEqual(self.due_records("2026-09-30"), [])
        records = self.due_records("2026-10-01")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["plan_id"], "P001")
        self.assertEqual(records[0]["overdue_days"], 0)
        self.assertEqual(self.due_records("2026-10-11")[0]["overdue_days"], 10)

    def test_duplicate_plan_id_same_asset_is_rejected(self) -> None:
        self.assertEqual(self.plan().returncode, 0)
        result = self.plan(task="另一项保养")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotEqual(result.stderr, "")
        self.assertEqual(result.stdout, "")
        records = self.due_records("2026-10-01")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["task"], "更换滤网")

    def test_same_plan_id_allowed_on_different_assets(self) -> None:
        self.invoke(
            "register",
            "--asset-id", "A002",
            "--name", "打印机",
            "--category", "办公设备",
            "--location", "办公室",
            "--purchase-date", "2026-09-25",
            "--purchase-amount", "1000",
        )
        self.assertEqual(self.plan("A001").returncode, 0)
        self.assertEqual(self.plan("A002").returncode, 0)
        self.assertEqual(len(self.due_records("2026-10-01")), 2)

    def test_plan_for_unknown_asset_is_rejected(self) -> None:
        result = self.plan("A404")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotEqual(result.stderr, "")
        self.assertEqual(result.stdout, "")
        self.assertEqual(self.due_records("2026-10-01"), [])

    def test_invalid_plan_input_is_rejected_without_changes(self) -> None:
        bad_calls = [
            {"plan-id": ""},
            {"task": ""},
            {"interval-days": "0"},
            {"interval-days": "-5"},
            {"interval-days": "1.5"},
            {"interval-days": "abc"},
            {"next-due": "2026/10/01"},
            {"next-due": "2026-02-30"},
        ]
        for overrides in bad_calls:
            with self.subTest(overrides=overrides):
                result = self.plan(**overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotEqual(result.stderr, "")
                self.assertEqual(result.stdout, "")
        self.assertEqual(self.due_records("2026-10-01"), [])

    def test_perform_advances_next_due(self) -> None:
        self.assertEqual(self.plan().returncode, 0)
        result = self.perform(performed_date="2026-10-05")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {"asset_id": "A001", "plan_id": "P001", "next_due": "2027-01-03"},
        )
        self.assertEqual(self.due_records("2026-10-05"), [])
        self.assertEqual(self.due_records("2027-01-03")[0]["overdue_days"], 0)

    def test_perform_before_next_due_is_rejected(self) -> None:
        self.assertEqual(self.plan().returncode, 0)
        result = self.perform(performed_date="2026-09-30")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotEqual(result.stderr, "")
        self.assertEqual(result.stdout, "")
        self.assertEqual(self.due_records("2026-10-01")[0]["next_due"], "2026-10-01")

    def test_perform_unknown_plan_is_rejected(self) -> None:
        result = self.perform(plan_id="P404")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotEqual(result.stderr, "")
        self.assertEqual(result.stdout, "")

    def test_due_records_sorted_by_asset_and_plan(self) -> None:
        self.invoke(
            "register",
            "--asset-id", "A002",
            "--name", "打印机",
            "--category", "办公设备",
            "--location", "办公室",
            "--purchase-date", "2026-09-25",
            "--purchase-amount", "1000",
        )
        self.plan("A002", "P002")
        self.plan("A001", "P002")
        self.plan("A001", "P001")
        self.plan("A002", "P001")
        keys = [(r["asset_id"], r["plan_id"]) for r in self.due_records("2026-10-01")]
        self.assertEqual(
            keys,
            [("A001", "P001"), ("A001", "P002"), ("A002", "P001"), ("A002", "P002")],
        )


if __name__ == "__main__":
    unittest.main()
