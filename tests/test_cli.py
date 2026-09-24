"""Checks for the documented command-line entry point."""

import datetime
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

    def register(self, amount: str = "1000.00", date_: str = "2021-03-15") -> None:
        result = self.invoke(
            "register",
            "--asset-id", "A001",
            "--name", "电脑",
            "--category", "办公设备",
            "--location", "办公室",
            "--purchase-date", date_,
            "--purchase-amount", amount,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def depreciation(self, *arguments: str) -> dict[str, object]:
        result = self.invoke("depreciation", *arguments)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 1)
        return json.loads(lines[0])

    def test_basic_depreciation_output(self) -> None:
        self.register()
        line = self.depreciation("--asset-id", "A001", "--as-of", "2021-04-15")
        self.assertEqual(
            line,
            {
                "asset_id": "A001",
                "purchase_amount": 1000.00,
                "residual_amount": 50.00,
                "as_of": "2021-04-15",
                "elapsed_months": 1,
                "monthly_depreciation": 15.83,
                "accumulated_depreciation": 15.83,
                "net_book_value": 984.17,
            },
        )

    def test_whole_months_only(self) -> None:
        self.register()
        # Same month, or short by one day: no whole month has elapsed.
        self.assertEqual(
            self.depreciation("--asset-id", "A001", "--as-of", "2021-03-15")[
                "elapsed_months"
            ],
            0,
        )
        self.assertEqual(
            self.depreciation("--asset-id", "A001", "--as-of", "2021-04-14")[
                "elapsed_months"
            ],
            0,
        )
        # One whole month, then six years and six months.
        self.assertEqual(
            self.depreciation("--asset-id", "A001", "--as-of", "2026-09-25")[
                "elapsed_months"
            ],
            66,
        )

    def test_as_of_before_purchase(self) -> None:
        self.register()
        line = self.depreciation("--asset-id", "A001", "--as-of", "2020-01-01")
        self.assertEqual(line["elapsed_months"], 0)
        self.assertEqual(line["accumulated_depreciation"], 0.00)
        self.assertEqual(line["net_book_value"], 1000.00)

    def test_net_book_value_never_below_residual(self) -> None:
        self.register()
        line = self.depreciation("--asset-id", "A001", "--as-of", "2031-01-01")
        self.assertEqual(line["elapsed_months"], 117)
        self.assertEqual(line["accumulated_depreciation"], 950.00)
        self.assertEqual(line["net_book_value"], 50.00)

    def test_half_up_rounding(self) -> None:
        # Annual depreciation 9.50 / 5 = 1.90, monthly = 0.15833... -> 0.16.
        self.register(amount="10.00")
        line = self.depreciation("--asset-id", "A001", "--as-of", "2021-04-15")
        self.assertEqual(line["residual_amount"], 0.50)
        self.assertEqual(line["monthly_depreciation"], 0.16)
        self.assertEqual(line["accumulated_depreciation"], 0.16)
        self.assertEqual(line["net_book_value"], 9.84)

    def test_default_as_of_is_today(self) -> None:
        self.register(date_=datetime.date.today().isoformat())
        result = self.invoke("depreciation", "--asset-id", "A001")
        self.assertEqual(result.returncode, 0, result.stderr)
        line = json.loads(result.stdout)
        self.assertEqual(line["as_of"], datetime.date.today().isoformat())
        self.assertEqual(line["elapsed_months"], 0)
        self.assertEqual(line["net_book_value"], 1000.00)

    def test_unknown_asset_id_is_an_error(self) -> None:
        result = self.invoke("depreciation", "--asset-id", "NOPE", "--as-of", "2026-09-25")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotEqual(result.stderr, "")
        self.assertEqual(result.stdout, "")

    def test_invalid_as_of_is_an_error(self) -> None:
        self.register()
        for bad_as_of in ("2026/09/25", "2026-13-01", "2026-02-30", "not-a-date"):
            with self.subTest(bad_as_of=bad_as_of):
                result = self.invoke(
                    "depreciation", "--asset-id", "A001", "--as-of", bad_as_of
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertNotEqual(result.stderr, "")
                self.assertEqual(result.stdout, "")

    def test_depreciation_is_read_only(self) -> None:
        self.register()
        self.depreciation("--asset-id", "A001", "--as-of", "2026-09-25")
        records = json.loads(self.invoke("query").stdout)["records"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["status"], "in_use")
        self.assertEqual(records[0]["location"], "办公室")
        self.assertEqual(records[0]["purchase_amount"], 1000.00)


if __name__ == "__main__":
    unittest.main()
