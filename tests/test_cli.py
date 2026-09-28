"""Checks for the documented command-line entry point."""

import json
import os
from pathlib import Path
import sqlite3
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


class DepreciationTests(unittest.TestCase):
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

    def depreciate(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return self.invoke(
            "depreciate",
            "--asset-id",
            "A001",
            "--as-of",
            "2027-09-25",
            "--annual-rate",
            "5",
            *arguments,
        )

    def register(self, **overrides: str) -> subprocess.CompletedProcess[str]:
        fields = {
            "asset-id": "A001",
            "name": "电脑",
            "category": "办公设备",
            "location": "办公室",
            "purchase-date": "2026-09-25",
            "purchase-amount": "8999.00",
        }
        fields.update(overrides)
        arguments = ["register"]
        for key, value in fields.items():
            arguments += [f"--{key}", value]
        return self.invoke(*arguments)

    def set_status(self, status: str) -> None:
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                "UPDATE assets SET status = ? WHERE asset_id = ?", (status, "A001")
            )

    def test_straight_line_depreciation_after_one_year(self) -> None:
        self.assertEqual(self.register().returncode, 0)
        result = self.depreciate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        line = json.loads(result.stdout)
        self.assertEqual(
            line,
            {
                "asset_id": "A001",
                "as_of": "2027-09-25",
                "purchase_amount": 8999.00,
                "annual_rate": 5.0,
                "accumulated_depreciation": 449.95,
                "net_value": 8549.05,
            },
        )
        self.assertEqual(
            result.stdout.strip(),
            '{"asset_id": "A001", "as_of": "2027-09-25", '
            '"purchase_amount": 8999.00, "annual_rate": 5.0, '
            '"accumulated_depreciation": 449.95, "net_value": 8549.05}',
        )

    def test_partial_year_counts_by_days(self) -> None:
        self.register()
        result = self.invoke(
            "depreciate",
            "--asset-id",
            "A001",
            "--as-of",
            "2026-10-25",
            "--annual-rate",
            "5",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        line = json.loads(result.stdout)
        # 30 days: 8999.00 * 0.05 * 30 / 365 = 36.9821... -> 36.98
        self.assertEqual(line["accumulated_depreciation"], 36.98)
        self.assertEqual(line["net_value"], 8962.02)
        self.assertEqual(line["annual_rate"], 5.0)

    def test_fractional_rate_is_accepted(self) -> None:
        self.register()
        result = self.invoke(
            "depreciate",
            "--asset-id",
            "A001",
            "--as-of",
            "2026-09-25",
            "--annual-rate",
            "2.5",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        line = json.loads(result.stdout)
        self.assertEqual(line["annual_rate"], 2.5)
        self.assertEqual(line["accumulated_depreciation"], 0.00)
        self.assertEqual(line["net_value"], 8999.00)

    def test_depreciation_is_capped_at_purchase_amount(self) -> None:
        self.register()
        result = self.invoke(
            "depreciate",
            "--asset-id",
            "A001",
            "--as-of",
            "2099-01-01",
            "--annual-rate",
            "100",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        line = json.loads(result.stdout)
        self.assertEqual(line["accumulated_depreciation"], 8999.00)
        self.assertEqual(line["net_value"], 0.00)

    def test_asset_not_in_use_can_still_depreciate(self) -> None:
        self.register()
        self.set_status("retired")
        result = self.depreciate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["net_value"], 8549.05)

    def test_error_cases(self) -> None:
        self.register()
        bad_calls = [
            ("A404", "2027-09-25", "5"),
            ("A001", "2025-09-25", "5"),  # before purchase date
            ("A001", "2027/09/25", "5"),
            ("A001", "2027-02-30", "5"),
            ("A001", "2027-09-25", "abc"),
            ("A001", "2027-09-25", "-1"),
            ("A001", "2027-09-25", "100.1"),
            ("A001", "2027-09-25", ""),
        ]
        for asset_id, as_of, rate in bad_calls:
            with self.subTest(call=(asset_id, as_of, rate)):
                result = self.invoke(
                    "depreciate",
                    "--asset-id",
                    asset_id,
                    "--as-of",
                    as_of,
                    "--annual-rate",
                    rate,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertNotEqual(result.stderr, "")
                self.assertEqual(result.stdout, "")

    def test_missing_required_argument_is_an_error(self) -> None:
        result = self.invoke(
            "depreciate", "--asset-id", "A001", "--as-of", "2027-09-25"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertNotEqual(result.stderr, "")
        self.assertEqual(result.stdout, "")

    def test_unsuccessful_depreciation_does_not_change_records(self) -> None:
        self.register()
        result = self.invoke(
            "depreciate",
            "--asset-id",
            "A001",
            "--as-of",
            "2025-01-01",
            "--annual-rate",
            "5",
        )
        self.assertNotEqual(result.returncode, 0)
        with sqlite3.connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT asset_id, status FROM assets"
            ).fetchall()
        self.assertEqual(row, [("A001", "in_use")])


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

    def register_asset(self, asset_id: str = "A001", **overrides: str) -> None:
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
        result = self.invoke(*arguments)
        self.assertEqual(result.returncode, 0, result.stderr)

    def register_plan(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        defaults = [
            "--asset-id", "A001",
            "--plan-id", "P001",
            "--item", "年度校准",
            "--cycle-days", "90",
            "--first-due", "2027-01-01",
        ]
        return self.invoke("register-plan", *(arguments or defaults))

    def reminders(self, start: str, end: str) -> list[dict[str, object]]:
        result = self.invoke("reminders", "--start", start, "--end", end)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)["records"]

    def test_register_plan_success_output(self) -> None:
        self.register_asset()
        result = self.register_plan()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout),
            {
                "plan_id": "P001",
                "asset_id": "A001",
                "item": "年度校准",
                "cycle_days": 90,
                "next_due": "2027-01-01",
            },
        )
        self.assertEqual(
            result.stdout.strip(),
            '{"plan_id": "P001", "asset_id": "A001", "item": "年度校准", '
            '"cycle_days": 90, "next_due": "2027-01-01"}',
        )

    def test_multiple_plans_per_asset_but_unique_item(self) -> None:
        self.register_asset()
        self.assertEqual(self.register_plan().returncode, 0)
        result = self.register_plan(
            "--asset-id", "A001",
            "--plan-id", "P002",
            "--item", "月度检查",
            "--cycle-days", "30",
            "--first-due", "2026-10-25",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        # Same item name on the same asset is rejected even with a new plan id.
        result = self.register_plan(
            "--asset-id", "A001",
            "--plan-id", "P003",
            "--item", "年度校准",
            "--cycle-days", "30",
            "--first-due", "2027-01-01",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertNotEqual(result.stderr, "")
        self.assertEqual(result.stdout, "")
        # Same item on a different asset is allowed.
        self.register_asset("A002")
        result = self.register_plan(
            "--asset-id", "A002",
            "--plan-id", "P003",
            "--item", "年度校准",
            "--cycle-days", "30",
            "--first-due", "2027-01-01",
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_register_plan_errors(self) -> None:
        self.register_asset()
        self.register_plan()
        bad_calls = [
            # duplicate plan id
            ("--asset-id", "A001", "--plan-id", "P001", "--item", "其他",
             "--cycle-days", "30", "--first-due", "2027-01-01"),
            # unknown asset
            ("--asset-id", "A404", "--plan-id", "P009", "--item", "x",
             "--cycle-days", "30", "--first-due", "2027-01-01"),
            # empty fields
            ("--asset-id", "", "--plan-id", "P009", "--item", "x",
             "--cycle-days", "30", "--first-due", "2027-01-01"),
            ("--asset-id", "A001", "--plan-id", "", "--item", "x",
             "--cycle-days", "30", "--first-due", "2027-01-01"),
            ("--asset-id", "A001", "--plan-id", "P009", "--item", "",
             "--cycle-days", "30", "--first-due", "2027-01-01"),
            # first due before purchase date
            ("--asset-id", "A001", "--plan-id", "P009", "--item", "x",
             "--cycle-days", "30", "--first-due", "2026-09-24"),
            # invalid dates
            ("--asset-id", "A001", "--plan-id", "P009", "--item", "x",
             "--cycle-days", "30", "--first-due", "2027/01/01"),
            ("--asset-id", "A001", "--plan-id", "P009", "--item", "x",
             "--cycle-days", "30", "--first-due", "2027-02-30"),
            # invalid cycle days
            ("--asset-id", "A001", "--plan-id", "P009", "--item", "x",
             "--cycle-days", "0", "--first-due", "2027-01-01"),
            ("--asset-id", "A001", "--plan-id", "P009", "--item", "x",
             "--cycle-days", "-5", "--first-due", "2027-01-01"),
            ("--asset-id", "A001", "--plan-id", "P009", "--item", "x",
             "--cycle-days", "1.5", "--first-due", "2027-01-01"),
            ("--asset-id", "A001", "--plan-id", "P009", "--item", "x",
             "--cycle-days", "abc", "--first-due", "2027-01-01"),
        ]
        for arguments in bad_calls:
            with self.subTest(arguments=arguments):
                result = self.register_plan(*arguments)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotEqual(result.stderr, "")
                self.assertEqual(result.stdout, "")
        with sqlite3.connect(self.db_path) as connection:
            rows = connection.execute("SELECT plan_id FROM maintenance_plans").fetchall()
        self.assertEqual(rows, [("P001",)])

    def test_first_due_equal_to_purchase_date_is_allowed(self) -> None:
        self.register_asset()
        result = self.register_plan(
            "--asset-id", "A001",
            "--plan-id", "P001",
            "--item", "验收",
            "--cycle-days", "365",
            "--first-due", "2026-09-25",
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_reminders_recurring_dates_and_order(self) -> None:
        self.register_asset()
        # P001: first due 2027-01-01, every 90 days -> 01-01, 04-01, 06-30...
        self.register_plan()
        # P002: first due 2026-10-25, every 30 days.
        self.register_plan(
            "--asset-id", "A001",
            "--plan-id", "P002",
            "--item", "月度检查",
            "--cycle-days", "30",
            "--first-due", "2026-10-25",
        )
        records = self.reminders("2027-02-01", "2027-04-30")
        self.assertEqual(
            records,
            [
                {"plan_id": "P002", "asset_id": "A001", "item": "月度检查",
                 "cycle_days": 30, "due_date": "2027-02-22"},
                {"plan_id": "P001", "asset_id": "A001", "item": "年度校准",
                 "cycle_days": 90, "due_date": "2027-04-01"},
            ],
        )
        # Inclusive endpoints: a due date exactly on the boundary is included.
        self.assertEqual(
            [r["due_date"] for r in self.reminders("2027-01-01", "2027-01-01")],
            ["2027-01-01"],
        )
        # Window with no occurrence -> empty records, exit 0.
        self.assertEqual(self.reminders("2027-02-23", "2027-03-23"), [])

    def test_reminders_empty_database(self) -> None:
        self.assertEqual(self.reminders("2027-01-01", "2027-12-31"), [])

    def test_reminders_same_day_sorted_by_plan_id(self) -> None:
        self.register_asset()
        for plan_id in ("P003", "P001", "P002"):
            result = self.register_plan(
                "--asset-id", "A001",
                "--plan-id", plan_id,
                "--item", f"项目{plan_id}",
                "--cycle-days", "30",
                "--first-due", "2027-01-15",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        records = self.reminders("2027-01-01", "2027-01-31")
        self.assertEqual([r["plan_id"] for r in records], ["P001", "P002", "P003"])

    def test_reminders_bad_range(self) -> None:
        for arguments in [
            ("--start", "2027-02-01", "--end", "2027-01-01"),
            ("--start", "2027/02/01", "--end", "2027-03-01"),
            ("--start", "2027-02-30", "--end", "2027-03-01"),
        ]:
            with self.subTest(arguments=arguments):
                result = self.invoke("reminders", *arguments)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotEqual(result.stderr, "")
                self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
