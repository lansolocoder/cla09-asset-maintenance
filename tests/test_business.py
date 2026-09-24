"""Business behaviour tests for register / move / show / list."""

import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REGISTER_ARGS = [
    "--tag", "A001",
    "--name", "MacBook Pro",
    "--category", "laptop",
    "--purchase-date", "2026-01-15",
    "--location", "Office A-12",
]


class LedgerCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "asset_ledger.db"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def invoke(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable, "-m", "asset_ledger",
                "--db", str(self.db_path),
                *arguments,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def register(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return self.invoke("register", *REGISTER_ARGS, *extra)

    def table_count(self, table: str) -> int:
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    # ---- registration -------------------------------------------------

    def test_reading_missing_db_creates_no_file(self) -> None:
        result = self.invoke("list")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertFalse(self.db_path.exists())

        result = self.invoke("show", "--tag", "X")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not found", result.stderr)
        self.assertFalse(self.db_path.exists())

    def test_failed_first_registration_creates_no_file(self) -> None:
        result = self.invoke(
            "register",
            "--tag", "A001", "--name", "X", "--category", "c",
            "--purchase-date", "2026-02-30", "--location", "Office",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("YYYY-MM-DD", result.stderr)
        self.assertFalse(self.db_path.exists())

    def test_register_success_output(self) -> None:
        result = self.register()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "Ledger ID: 1\nAsset tag: A001\nInitial location: Office A-12\n",
        )
        self.assertEqual(result.stderr, "")

    def test_register_missing_required_field(self) -> None:
        result = self.invoke(
            "register",
            "--tag", "A002", "--name", "X", "--category", "c",
            "--purchase-date", "2026-01-15",
            # --location omitted
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--location", result.stderr)
        self.assertFalse(self.db_path.exists())

    def test_register_blank_required_field(self) -> None:
        result = self.invoke(
            "register",
            "--tag", "  ", "--name", "X", "--category", "c",
            "--purchase-date", "2026-01-15", "--location", "L",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("asset tag is required", result.stderr)

    def test_duplicate_tag_rejected_without_changing_data(self) -> None:
        self.assertEqual(self.register().returncode, 0)
        result = self.invoke(
            "register",
            "--tag", "A001", "--name", "Other", "--category", "laptop",
            "--purchase-date", "2026-02-15", "--location", "Office B",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already exists", result.stderr)
        self.assertEqual(self.table_count("assets"), 1)
        # Original row untouched.
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT name, current_location FROM assets WHERE tag = 'A001'"
            ).fetchone()
        self.assertEqual(row, ("MacBook Pro", "Office A-12"))

    def test_bad_purchase_date_variants(self) -> None:
        for bad in ["2026/01/15", "2026-1-15", "20260115", "2026-02-30", "not-a-date"]:
            with self.subTest(bad=bad):
                result = self.invoke(
                    "register",
                    "--tag", f"T-{bad}", "--name", "X", "--category", "c",
                    "--purchase-date", bad, "--location", "L",
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("YYYY-MM-DD", result.stderr)

    # ---- idempotency --------------------------------------------------

    def test_identical_request_id_replays_same_result(self) -> None:
        first = self.register("--request-id", "req-1")
        second = self.register("--request-id", "req-1")
        self.assertEqual(first.returncode, 0)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(self.table_count("assets"), 1)
        self.assertEqual(self.table_count("registrations"), 1)

    def test_same_request_id_different_payload_rejected(self) -> None:
        self.assertEqual(self.register("--request-id", "req-1").returncode, 0)
        result = self.invoke(
            "register",
            "--tag", "A001", "--name", "Different Name", "--category", "laptop",
            "--purchase-date", "2026-01-15", "--location", "Office A-12",
            "--request-id", "req-1",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("req-1", result.stderr)
        self.assertIn("different", result.stderr)
        self.assertEqual(self.table_count("assets"), 1)

    def test_failed_attempt_does_not_poison_request_id(self) -> None:
        bad = self.invoke(
            "register",
            "--tag", "A999", "--name", "X", "--category", "c",
            "--purchase-date", "bad-date", "--location", "L",
            "--request-id", "req-2",
        )
        self.assertNotEqual(bad.returncode, 0)
        good = self.invoke(
            "register",
            "--tag", "A002", "--name", "ThinkPad", "--category", "laptop",
            "--purchase-date", "2026-03-01", "--location", "Storage",
            "--request-id", "req-2",
        )
        self.assertEqual(good.returncode, 0, good.stderr)
        self.assertEqual(self.table_count("assets"), 1)
        # The accepted request id replays its own original result (the failed
        # attempt did not consume an asset id either).
        replay = self.invoke(
            "register",
            "--tag", "A002", "--name", "ThinkPad", "--category", "laptop",
            "--purchase-date", "2026-03-01", "--location", "Storage",
            "--request-id", "req-2",
        )
        self.assertEqual(replay.returncode, 0, replay.stderr)
        self.assertEqual(replay.stdout, good.stdout)
        self.assertIn("Ledger ID: 1", replay.stdout)

    # ---- location changes ---------------------------------------------

    def test_move_success_appends_history(self) -> None:
        self.register()
        result = self.invoke(
            "move", "--tag", "A001", "--location", "Warehouse",
            "--date", "2026-02-01", "--note", "deploy",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "Current location: Warehouse\n")
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT current_location FROM assets WHERE tag='A001'"
                ).fetchone()[0],
                "Warehouse",
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM location_history").fetchone()[0],
                1,
            )

    def test_move_failures_leave_state_untouched(self) -> None:
        self.register()
        self.invoke(
            "move", "--tag", "A001", "--location", "Warehouse", "--date", "2026-02-01"
        )

        cases = [
            (
                "missing asset",
                ["move", "--tag", "NOPE", "--location", "X", "--date", "2026-02-02"],
                "not found",
            ),
            (
                "same location",
                ["move", "--tag", "A001", "--location", "Warehouse", "--date", "2026-02-02"],
                "same",
            ),
            (
                "bad date",
                ["move", "--tag", "A001", "--location", "Office", "--date", "2026-02-30"],
                "YYYY-MM-DD",
            ),
            (
                "earlier date",
                ["move", "--tag", "A001", "--location", "Office", "--date", "2026-01-20"],
                "earlier",
            ),
        ]
        for label, argv, expected in cases:
            with self.subTest(label):
                result = self.invoke(*argv)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)

        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT current_location FROM assets WHERE tag='A001'"
                ).fetchone()[0],
                "Warehouse",
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM location_history").fetchone()[0],
                1,
            )

    def test_history_is_ordered_and_immutable(self) -> None:
        self.register()
        self.invoke("move", "--tag", "A001", "--location", "B", "--date", "2026-02-01")
        self.invoke("move", "--tag", "A001", "--location", "C", "--date", "2026-03-01", "--note", "repair")
        self.invoke("move", "--tag", "A001", "--location", "D", "--date", "2026-03-01")
        show = self.invoke("show", "--tag", "A001")
        self.assertEqual(show.returncode, 0)
        lines = [line for line in show.stdout.splitlines() if line.startswith("  2026")]
        self.assertEqual(
            lines,
            [
                "  2026-02-01: Office A-12 -> B",
                "  2026-03-01: B -> C | note: repair",
                "  2026-03-01: C -> D",
            ],
        )

    # ---- show / list ---------------------------------------------------

    def test_show_without_history(self) -> None:
        self.register()
        result = self.invoke("show", "--tag", "A001")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Asset tag: A001", result.stdout)
        self.assertIn("Name: MacBook Pro", result.stdout)
        self.assertIn("Category: laptop", result.stdout)
        self.assertIn("Purchase date: 2026-01-15", result.stdout)
        self.assertIn("Current location: Office A-12", result.stdout)
        self.assertIn("(no location changes recorded)", result.stdout)

    def test_show_missing_asset(self) -> None:
        self.register()
        result = self.invoke("show", "--tag", "ZZZ")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not found", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_list_order_and_category_filter(self) -> None:
        specs = [
            ("A001", "Laptop One", "laptop", "2026-01-15", "Office"),
            ("D001", "Display One", "display", "2026-02-15", "Shelf"),
            ("A002", "Laptop Two", "laptop", "2026-03-15", "Home"),
        ]
        for tag, name, cat, date, loc in specs:
            result = self.invoke(
                "register", "--tag", tag, "--name", name, "--category", cat,
                "--purchase-date", date, "--location", loc,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

        result = self.invoke("list")
        ids = [line.split("]")[0] + "]" for line in result.stdout.splitlines()]
        self.assertEqual(ids, ["[1]", "[2]", "[3]"])
        self.assertIn("A001", result.stdout)
        self.assertIn("D001", result.stdout)

        filtered = self.invoke("list", "--category", "display")
        self.assertEqual(filtered.stdout.count("\n"), 1)
        self.assertIn("D001", filtered.stdout)
        self.assertNotIn("A001", filtered.stdout)

    def test_db_flag_works_on_both_sides_of_subcommand(self) -> None:
        self.assertEqual(self.register().returncode, 0)
        before = subprocess.run(
            [sys.executable, "-m", "asset_ledger", "list", "--db", str(self.db_path)],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(before.returncode, 0, before.stderr)
        self.assertIn("A001", before.stdout)


if __name__ == "__main__":
    unittest.main()
