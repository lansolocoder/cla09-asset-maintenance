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


class DepreciationSummaryTests(unittest.TestCase):
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

    def register(
        self,
        asset_id: str,
        amount: str = "1000.00",
        date_: str = "2021-03-15",
    ) -> None:
        result = self.invoke(
            "register",
            "--asset-id", asset_id,
            "--name", "电脑",
            "--category", "办公设备",
            "--location", "办公室",
            "--purchase-date", date_,
            "--purchase-amount", amount,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def summary(self, month: str) -> dict[str, object]:
        result = self.invoke("depreciation-summary", "--month", month)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 1)
        return json.loads(lines[0])

    def test_empty_ledger(self) -> None:
        line = self.summary("2026-09")
        self.assertEqual(line["month"], "2026-09")
        self.assertEqual(line["records"], [])
        self.assertEqual(
            line["totals"],
            {
                "total_monthly": 0.00,
                "total_accumulated": 0.00,
                "total_net_book_value": 0.00,
            },
        )

    def test_records_sorted_and_totalled(self) -> None:
        self.register("B002", amount="1000.00", date_="2021-03-15")
        self.register("A001", amount="10.00", date_="2015-01-10")
        self.register("C003", amount="123.40", date_="2026-09-25")
        line = self.summary("2021-04")
        self.assertEqual(
            [r["asset_id"] for r in line["records"]], ["A001", "B002", "C003"]
        )
        a001, b002, c003 = line["records"]
        # A001 long fully depreciated (month 75): no current charge, at residual.
        self.assertEqual(
            a001,
            {
                "asset_id": "A001",
                "purchase_amount": 10.00,
                "monthly_depreciation": 0.00,
                "accumulated_depreciation": 9.50,
                "net_book_value": 0.50,
            },
        )
        # B002: first whole month completed 2021-04-15.
        self.assertEqual(
            b002,
            {
                "asset_id": "B002",
                "purchase_amount": 1000.00,
                "monthly_depreciation": 15.83,
                "accumulated_depreciation": 15.83,
                "net_book_value": 984.17,
            },
        )
        # C003 purchased far in the future: nothing charged yet.
        self.assertEqual(
            c003,
            {
                "asset_id": "C003",
                "purchase_amount": 123.40,
                "monthly_depreciation": 0.00,
                "accumulated_depreciation": 0.00,
                "net_book_value": 123.40,
            },
        )
        self.assertEqual(
            line["totals"],
            {
                "total_monthly": 15.83,
                "total_accumulated": 25.33,
                "total_net_book_value": 1108.07,
            },
        )

    def test_purchase_month_is_not_charged(self) -> None:
        self.register("A001", date_="2021-03-15")
        line = self.summary("2021-03")
        record = line["records"][0]
        self.assertEqual(record["monthly_depreciation"], 0.00)
        self.assertEqual(record["accumulated_depreciation"], 0.00)
        self.assertEqual(record["net_book_value"], 1000.00)

    def test_month_before_purchase(self) -> None:
        self.register("A001", date_="2021-03-15")
        line = self.summary("2020-12")
        record = line["records"][0]
        self.assertEqual(record["monthly_depreciation"], 0.00)
        self.assertEqual(record["accumulated_depreciation"], 0.00)
        self.assertEqual(record["net_book_value"], 1000.00)

    def test_final_month_is_capped_then_zero(self) -> None:
        # Monthly 0.16, but the depreciable total is 9.50, so month 60 is 0.06.
        self.register("A001", amount="10.00", date_="2015-01-10")
        month_59 = self.summary("2019-12")["records"][0]
        self.assertEqual(month_59["monthly_depreciation"], 0.16)
        self.assertEqual(month_59["accumulated_depreciation"], 9.44)
        month_60 = self.summary("2020-01")["records"][0]
        self.assertEqual(month_60["monthly_depreciation"], 0.06)
        self.assertEqual(month_60["accumulated_depreciation"], 9.50)
        self.assertEqual(month_60["net_book_value"], 0.50)
        after = self.summary("2020-02")["records"][0]
        self.assertEqual(after["monthly_depreciation"], 0.00)
        self.assertEqual(after["accumulated_depreciation"], 9.50)
        self.assertEqual(after["net_book_value"], 0.50)

    def test_matches_single_depreciation_at_month_end(self) -> None:
        self.register("A001", date_="2021-03-15")
        record = self.summary("2025-09")["records"][0]
        single = json.loads(
            self.invoke(
                "depreciation", "--asset-id", "A001", "--as-of", "2025-09-30"
            ).stdout
        )
        self.assertEqual(record["purchase_amount"], single["purchase_amount"])
        self.assertEqual(
            record["accumulated_depreciation"], single["accumulated_depreciation"]
        )
        self.assertEqual(record["net_book_value"], single["net_book_value"])
        # 54 whole months: 15.83 * 54 = 854.82.
        self.assertEqual(record["monthly_depreciation"], 15.83)
        self.assertEqual(record["accumulated_depreciation"], 854.82)
        self.assertEqual(record["net_book_value"], 145.18)

    def test_amounts_are_two_place_numbers(self) -> None:
        self.register("A001", amount="1000.00", date_="2021-03-15")
        out = self.invoke("depreciation-summary", "--month", "2021-04").stdout
        for token in ("1000.00", "15.83", "984.17"):
            self.assertIn(token, out)
        record = json.loads(out)["records"][0]
        for key in (
            "purchase_amount",
            "monthly_depreciation",
            "accumulated_depreciation",
            "net_book_value",
        ):
            self.assertIsInstance(record[key], float)

    def test_invalid_month_is_an_error(self) -> None:
        self.register("A001")
        for bad_month in ("2026/09", "2026-13", "2026-00", "2026-9", "abc", ""):
            with self.subTest(bad_month=bad_month):
                result = self.invoke("depreciation-summary", "--month", bad_month)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotEqual(result.stderr, "")
                self.assertEqual(result.stdout, "")

    def test_missing_month_is_an_error(self) -> None:
        result = self.invoke("depreciation-summary")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--month", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_summary_is_read_only(self) -> None:
        self.register("A001")
        self.summary("2026-09")
        records = json.loads(self.invoke("query").stdout)["records"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["status"], "in_use")
        self.assertEqual(records[0]["purchase_amount"], 1000.00)


if __name__ == "__main__":
    unittest.main()
