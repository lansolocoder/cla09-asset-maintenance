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

    def register(self, asset_id: str = "A001", amount: str = "8999.00",
                 purchase_date: str = "2026-09-25") -> None:
        result = self.invoke(
            "register",
            "--asset-id", asset_id,
            "--name", "电脑",
            "--category", "办公设备",
            "--location", "办公室",
            "--purchase-date", purchase_date,
            "--purchase-amount", amount,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def depreciate(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return self.invoke(
            "depreciate",
            "--asset-id", "A001",
            "--as-of", "2027-09-25",
            "--useful-life-years", "5",
            "--salvage-rate", "0.10",
            *arguments,
        )

    def test_documented_example(self) -> None:
        self.register()
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
                "salvage_value": 899.90,
                "accumulated_depreciation": 1619.82,
                "book_value": 7379.18,
            },
        )

    def test_amounts_render_with_two_decimals(self) -> None:
        self.register()
        result = self.depreciate()
        for key in (
            "purchase_amount",
            "salvage_value",
            "accumulated_depreciation",
            "book_value",
        ):
            with self.subTest(key=key):
                self.assertRegex(result.stdout, rf'"{key}": -?\d+\.\d{{2}}')

    def test_as_of_on_or_before_purchase_date_is_zero(self) -> None:
        self.register()
        for as_of in ("2026-09-24", "2026-09-25"):
            with self.subTest(as_of=as_of):
                result = self.depreciate("--as-of", as_of)
                self.assertEqual(result.returncode, 0, result.stderr)
                line = json.loads(result.stdout)
                self.assertEqual(line["accumulated_depreciation"], 0.00)
                self.assertEqual(line["book_value"], 8999.00)

    def test_depreciation_starts_the_day_after_purchase(self) -> None:
        self.register()
        result = self.depreciate("--as-of", "2026-09-26")
        self.assertEqual(result.returncode, 0, result.stderr)
        line = json.loads(result.stdout)
        self.assertEqual(line["accumulated_depreciation"], 4.44)
        self.assertEqual(line["book_value"], 8994.56)

    def test_depreciation_is_capped_at_end_of_useful_life(self) -> None:
        self.register()
        for as_of in ("2031-09-25", "2040-01-01"):
            with self.subTest(as_of=as_of):
                result = self.depreciate("--as-of", as_of)
                line = json.loads(result.stdout)
                self.assertEqual(line["accumulated_depreciation"], 8099.10)
                self.assertEqual(line["book_value"], 899.90)

    def test_salvage_rate_endpoints(self) -> None:
        self.register()
        zero = self.depreciate("--salvage-rate", "0.00")
        self.assertEqual(json.loads(zero.stdout)["salvage_value"], 0.00)
        one = self.depreciate("--salvage-rate", "1.00")
        line = json.loads(one.stdout)
        self.assertEqual(line["salvage_value"], 8999.00)
        self.assertEqual(line["accumulated_depreciation"], 0.00)
        self.assertEqual(line["book_value"], 8999.00)

    def test_repeated_queries_are_read_only(self) -> None:
        self.register()
        first = self.depreciate().stdout
        second = self.depreciate()
        self.assertEqual(second.stdout, first)
        result = self.invoke("query")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)["records"]), 1)

    def test_unknown_asset_is_rejected_without_changes(self) -> None:
        self.register()
        result = self.depreciate("--asset-id", "NOPE")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertNotEqual(result.stderr, "")
        records = json.loads(self.invoke("query").stdout)["records"]
        self.assertEqual([r["asset_id"] for r in records], ["A001"])

    def test_invalid_arguments_are_rejected(self) -> None:
        self.register()
        bad_arguments = [
            ("--as-of", "2027/09/25"),
            ("--as-of", "2027-13-01"),
            ("--useful-life-years", "0"),
            ("--useful-life-years", "-5"),
            ("--useful-life-years", "5.0"),
            ("--useful-life-years", "abc"),
            ("--salvage-rate", "0.1"),
            ("--salvage-rate", "1.01"),
            ("--salvage-rate", "-0.10"),
            ("--salvage-rate", ".10"),
            ("--salvage-rate", "abc"),
        ]
        for override in bad_arguments:
            with self.subTest(override=override):
                result = self.depreciate(*override)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
                self.assertNotEqual(result.stderr, "")
        records = json.loads(self.invoke("query").stdout)["records"]
        self.assertEqual(len(records), 1)

    def test_missing_required_argument_is_an_error(self) -> None:
        result = self.invoke(
            "depreciate", "--asset-id", "A001", "--as-of", "2027-09-25"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertNotEqual(result.stderr, "")
        self.assertEqual(result.stdout, "")


class MoveTests(unittest.TestCase):
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

    def register(self, asset_id: str = "A001", location: str = "办公室",
                 purchase_date: str = "2026-09-25") -> None:
        result = self.invoke(
            "register",
            "--asset-id", asset_id,
            "--name", "电脑",
            "--category", "办公设备",
            "--location", location,
            "--purchase-date", purchase_date,
            "--purchase-amount", "8999.00",
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def move(self, to_location: str, effective_date: str = "2027-01-15",
             asset_id: str = "A001") -> subprocess.CompletedProcess[str]:
        return self.invoke(
            "move",
            "--asset-id", asset_id,
            "--to-location", to_location,
            "--effective-date", effective_date,
        )

    def moves_of(self, asset_id: str = "A001") -> list[dict[str, object]]:
        result = self.invoke("moves", "--asset-id", asset_id)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)["records"]

    def query_records(self) -> list[dict[str, object]]:
        result = self.invoke("query")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)["records"]

    def test_move_outputs_fact_and_updates_location(self) -> None:
        self.register()
        result = self.move("仓库C")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout),
            {
                "asset_id": "A001",
                "from_location": "办公室",
                "to_location": "仓库C",
                "effective_date": "2027-01-15",
                "sequence": 1,
            },
        )
        records = self.query_records()
        self.assertEqual(records[0]["location"], "仓库C")
        self.assertEqual(records[0]["move_count"], 1)

    def test_never_moved_asset_has_zero_move_count_and_empty_facts(self) -> None:
        self.register()
        self.assertEqual(self.query_records()[0]["move_count"], 0)
        self.assertEqual(self.moves_of(), [])

    def test_sequence_increments_and_facts_are_listed_in_order(self) -> None:
        self.register()
        self.assertEqual(json.loads(self.move("仓库C").stdout)["sequence"], 1)
        self.assertEqual(json.loads(self.move("实验室A").stdout)["sequence"], 2)
        self.assertEqual(json.loads(self.move("办公室").stdout)["sequence"], 3)
        self.assertEqual(
            self.moves_of(),
            [
                {"from_location": "办公室", "to_location": "仓库C",
                 "effective_date": "2027-01-15", "sequence": 1},
                {"from_location": "仓库C", "to_location": "实验室A",
                 "effective_date": "2027-01-15", "sequence": 2},
                {"from_location": "实验室A", "to_location": "办公室",
                 "effective_date": "2027-01-15", "sequence": 3},
            ],
        )
        self.assertEqual(self.query_records()[0]["move_count"], 3)

    def test_multiple_moves_on_same_effective_date_are_kept(self) -> None:
        self.register()
        self.move("仓库C", "2027-01-15")
        self.move("实验室A", "2027-01-15")
        facts = self.moves_of()
        self.assertEqual([f["sequence"] for f in facts], [1, 2])
        self.assertEqual({f["effective_date"] for f in facts}, {"2027-01-15"})
        # 最后落库一条决定当前位置
        self.assertEqual(self.query_records()[0]["location"], "实验室A")

    def test_moving_back_to_a_previous_location_is_recorded(self) -> None:
        self.register()
        self.move("仓库C")
        result = self.move("办公室")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["sequence"], 2)
        self.assertEqual(len(self.moves_of()), 2)

    def test_query_location_filter_uses_current_location(self) -> None:
        self.register()
        self.move("仓库C")
        self.assertEqual(
            [r["asset_id"] for r in json.loads(
                self.invoke("query", "--location", "仓库C").stdout)["records"]],
            ["A001"],
        )
        self.assertEqual(
            json.loads(self.invoke("query", "--location", "办公室").stdout)["records"],
            [],
        )

    def test_to_location_is_trimmed(self) -> None:
        self.register()
        result = self.move("  仓库C  ")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["to_location"], "仓库C")

    def test_effective_date_equal_to_purchase_date_is_allowed(self) -> None:
        self.register()
        result = self.move("仓库C", "2026-09-25")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["sequence"], 1)

    def test_invalid_moves_are_rejected_without_changes(self) -> None:
        self.register()
        bad_calls = [
            ("   ", "2027-01-15"),
            ("仓库C", "2027/01/15"),
            ("仓库C", "2027-13-40"),
            ("仓库C", "2026-09-24"),
            ("办公室", "2027-01-15"),  # 与当前位置相同
        ]
        for to_location, effective_date in bad_calls:
            with self.subTest(to_location=to_location, effective_date=effective_date):
                result = self.move(to_location, effective_date)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
                self.assertNotEqual(result.stderr, "")
        # 位置与变更记录均保持登记时的状态
        records = self.query_records()
        self.assertEqual(records[0]["location"], "办公室")
        self.assertEqual(records[0]["move_count"], 0)
        self.assertEqual(self.moves_of(), [])

    def test_move_unknown_asset_is_rejected_without_changes(self) -> None:
        result = self.move("仓库C", asset_id="NOPE")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertNotEqual(result.stderr, "")
        self.assertEqual(self.query_records(), [])

    def test_moves_unknown_asset_outputs_no_records(self) -> None:
        result = self.invoke("moves", "--asset-id", "NOPE")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertNotEqual(result.stderr, "")

    def test_missing_required_argument_is_an_error(self) -> None:
        result = self.invoke("move", "--asset-id", "A001", "--to-location", "仓库C")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertNotEqual(result.stderr, "")

    def test_depreciate_output_is_unchanged_after_move(self) -> None:
        self.register()
        self.move("仓库C")
        result = self.invoke(
            "depreciate",
            "--asset-id", "A001",
            "--as-of", "2027-09-25",
            "--useful-life-years", "5",
            "--salvage-rate", "0.10",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "asset_id": "A001",
                "as_of": "2027-09-25",
                "purchase_amount": 8999.00,
                "salvage_value": 899.90,
                "accumulated_depreciation": 1619.82,
                "book_value": 7379.18,
            },
        )


if __name__ == "__main__":
    unittest.main()
