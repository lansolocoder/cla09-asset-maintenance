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

    def register_asset(self, asset_id: str = "A001") -> subprocess.CompletedProcess[str]:
        return self.invoke(
            "register",
            "--asset-id",
            asset_id,
            "--name",
            "示波器",
            "--category",
            "仪器",
            "--location",
            "实验室B",
            "--purchase-date",
            "2026-09-01",
            "--purchase-amount",
            "123.40",
        )

    def create_plan(self, asset_id: str = "A001", plan_id: str = "P01",
                    **overrides: str) -> subprocess.CompletedProcess[str]:
        fields = {
            "asset-id": asset_id,
            "plan-id": plan_id,
            "task": "清灰检查",
            "interval-days": "30",
            "next-due": "2026-09-10",
        }
        fields.update(overrides)
        arguments = ["create-plan"]
        for key, value in fields.items():
            arguments += [f"--{key}", value]
        return self.invoke(*arguments)

    def record(self, asset_id: str, plan_id: str,
               execution_date: str) -> subprocess.CompletedProcess[str]:
        return self.invoke(
            "record-maintenance",
            "--asset-id",
            asset_id,
            "--plan-id",
            plan_id,
            "--execution-date",
            execution_date,
        )

    def due_records(self, query_date: str) -> list[dict[str, object]]:
        result = self.invoke("due", query_date)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)["records"]

    def test_create_plan_outputs_json_line(self) -> None:
        self.assertEqual(self.register_asset().returncode, 0)
        result = self.create_plan()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout),
            {
                "asset_id": "A001",
                "plan_id": "P01",
                "task": "清灰检查",
                "interval_days": 30,
                "next_due": "2026-09-10",
            },
        )

    def test_duplicate_plan_same_asset_is_rejected_unchanged(self) -> None:
        self.register_asset()
        self.assertEqual(self.create_plan().returncode, 0)
        result = self.create_plan(task="其他保养", **{"interval-days": "10"})
        self.assertNotEqual(result.returncode, 0)
        self.assertNotEqual(result.stderr, "")
        self.assertEqual(result.stdout, "")
        records = self.due_records("2026-12-31")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["task"], "清灰检查")
        self.assertEqual(records[0]["interval_days"], 30)
        self.assertEqual(records[0]["next_due"], "2026-09-10")

    def test_same_plan_id_for_different_assets_allowed(self) -> None:
        self.register_asset("A001")
        self.register_asset("A002")
        self.assertEqual(self.create_plan("A001", "P01").returncode, 0)
        result = self.create_plan("A002", "P01", task="换墨", **{"next-due": "2026-09-20"})
        self.assertEqual(result.returncode, 0, result.stderr)
        records = self.due_records("2026-12-31")
        self.assertEqual(
            [(r["asset_id"], r["plan_id"]) for r in records],
            [("A001", "P01"), ("A002", "P01")],
        )

    def test_plan_for_nonexistent_asset_is_rejected(self) -> None:
        result = self.create_plan("A999")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotEqual(result.stderr, "")
        self.assertEqual(result.stdout, "")
        self.assertEqual(self.due_records("2026-12-31"), [])

    def test_create_plan_validation_failures_change_nothing(self) -> None:
        self.register_asset()
        bad_calls = [
            {"task": ""},
            {"task": "   "},
            {"interval-days": "0"},
            {"interval-days": "-5"},
            {"interval-days": "3.5"},
            {"interval-days": "abc"},
            {"next-due": "2026/09/10"},
            {"next-due": "2026-09-31"},
            {"next-due": "not-a-date"},
        ]
        for overrides in bad_calls:
            with self.subTest(overrides=overrides):
                result = self.create_plan(**overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotEqual(result.stderr, "")
                self.assertEqual(result.stdout, "")
        self.assertEqual(self.due_records("2026-12-31"), [])

    def test_record_maintenance_advances_next_due(self) -> None:
        self.register_asset()
        self.create_plan()
        result = self.record("A001", "P01", "2026-09-10")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {"asset_id": "A001", "plan_id": "P01", "next_due": "2026-10-10"},
        )
        result = self.record("A001", "P01", "2026-11-05")
        self.assertEqual(json.loads(result.stdout)["next_due"], "2026-12-05")

    def test_record_maintenance_early_is_rejected_unchanged(self) -> None:
        self.register_asset()
        self.create_plan()
        result = self.record("A001", "P01", "2026-09-09")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotEqual(result.stderr, "")
        self.assertEqual(result.stdout, "")
        records = self.due_records("2026-09-10")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["next_due"], "2026-09-10")
        self.assertEqual(records[0]["overdue_days"], 0)

    def test_record_maintenance_unknown_plan_is_rejected(self) -> None:
        self.register_asset()
        self.create_plan()
        result = self.record("A001", "NOPE", "2026-09-10")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotEqual(result.stderr, "")
        self.assertEqual(result.stdout, "")
        result = self.record("A404", "P01", "2026-09-10")
        self.assertNotEqual(result.returncode, 0)

    def test_record_maintenance_invalid_date_is_rejected(self) -> None:
        self.register_asset()
        self.create_plan()
        result = self.record("A001", "P01", "2026-13-01")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        records = self.due_records("2026-09-10")
        self.assertEqual(records[0]["next_due"], "2026-09-10")

    def test_due_filters_ordering_and_overdue_days(self) -> None:
        self.register_asset("A002")
        self.register_asset("A001")
        self.create_plan("A002", "P02", **{"next-due": "2026-09-05"})
        self.create_plan("A002", "P01", **{"next-due": "2026-09-25"})
        self.create_plan("A001", "P01", **{"next-due": "2026-09-25"})
        self.create_plan("A001", "P99", **{"next-due": "2026-09-26"})

        records = self.due_records("2026-09-25")
        self.assertEqual(
            [(r["asset_id"], r["plan_id"]) for r in records],
            [("A001", "P01"), ("A002", "P01"), ("A002", "P02")],
        )
        by_key = {(r["asset_id"], r["plan_id"]): r for r in records}
        self.assertEqual(by_key[("A001", "P01")]["overdue_days"], 0)
        self.assertEqual(by_key[("A002", "P01")]["overdue_days"], 0)
        self.assertEqual(by_key[("A002", "P02")]["overdue_days"], 20)
        for record in records:
            self.assertEqual(
                set(record),
                {
                    "asset_id",
                    "plan_id",
                    "task",
                    "interval_days",
                    "next_due",
                    "overdue_days",
                },
            )
            self.assertIsInstance(record["overdue_days"], int)

    def test_due_empty_result(self) -> None:
        self.register_asset()
        self.create_plan(**{"next-due": "2026-09-10"})
        result = self.invoke("due", "2026-09-09")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {"records": []})

    def test_due_invalid_date_is_rejected(self) -> None:
        result = self.invoke("due", "2026-09-31")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotEqual(result.stderr, "")
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
