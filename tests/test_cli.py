"""Checks for the documented command-line entry point."""

from datetime import date, timedelta
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


class AssetCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "ledger.db"

    def invoke(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ, ASSET_LEDGER_DB=str(self.db_path))
        return subprocess.run(
            [sys.executable, "-m", "asset_ledger", *arguments],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

    def add_asset(self, asset_id: str = "A001") -> subprocess.CompletedProcess[str]:
        return self.invoke(
            "asset", "add", "--id", asset_id, "--name", "投影仪",
            "--cost", "3200", "--purchased-at", "2026-09-01",
        )

    def show(self, asset_id: str = "A001") -> dict:
        result = self.invoke("asset", "show", "--id", asset_id)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_add_and_show(self) -> None:
        result = self.add_asset()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "asset A001 registered")
        payload = self.show()
        self.assertEqual(payload["id"], "A001")
        self.assertEqual(payload["name"], "投影仪")
        self.assertEqual(payload["cost"], 3200)
        self.assertEqual(payload["purchased_at"], "2026-09-01")
        self.assertEqual(payload["status"], "in_use")
        self.assertEqual(len(payload["history"]), 1)
        self.assertEqual(payload["history"][0]["status"], "in_use")
        self.assertIsNone(payload["history"][0]["reason"])

    def test_add_rejects_duplicate_and_bad_input(self) -> None:
        self.assertEqual(self.add_asset().returncode, 0)
        before = self.show()
        for extra in [
            ("--id", "A001", "--name", "x", "--cost", "1", "--purchased-at", "2026-01-01"),
            ("--id", "B001", "--name", "x", "--cost", "0", "--purchased-at", "2026-01-01"),
            ("--id", "B001", "--name", "x", "--cost", "-5", "--purchased-at", "2026-01-01"),
            ("--id", "B001", "--name", "x", "--cost", "abc", "--purchased-at", "2026-01-01"),
            ("--id", "B001", "--name", "x", "--cost", "1", "--purchased-at", "2026-13-01"),
            ("--id", "B001", "--name", "x", "--cost", "1", "--purchased-at", "2999-01-01"),
        ]:
            with self.subTest(extra=extra):
                result = self.invoke("asset", "add", *extra)
                self.assertEqual(result.returncode, 1)
                self.assertNotEqual(result.stderr, "")
        self.assertEqual(self.show(), before)
        missing = self.invoke("asset", "show", "--id", "B001")
        self.assertEqual(missing.returncode, 1)
        self.assertEqual(missing.stdout, "")

    def test_status_transitions_and_history(self) -> None:
        self.assertEqual(self.add_asset().returncode, 0)
        result = self.invoke("asset", "status", "--id", "A001", "--to", "repairing")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "asset A001 repairing")
        self.assertEqual(
            self.invoke("asset", "status", "--id", "A001", "--to", "in_use").returncode, 0
        )
        result = self.invoke(
            "asset", "status", "--id", "A001", "--to", "retired", "--reason", " 报废 "
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = self.show()
        self.assertEqual(payload["status"], "retired")
        self.assertEqual(
            [entry["status"] for entry in payload["history"]],
            ["in_use", "repairing", "in_use", "retired"],
        )
        self.assertEqual(payload["history"][-1]["reason"], "报废")

    def test_status_rejections_leave_no_trace(self) -> None:
        self.assertEqual(self.add_asset().returncode, 0)
        before = self.show()
        rejections = [
            ("--id", "A001", "--to", "in_use"),                    # self transition
            ("--id", "A001", "--to", "broken"),                   # unknown status
            ("--id", "A001", "--to", "retired"),                  # missing reason
            ("--id", "A001", "--to", "retired", "--reason", "  "),  # blank reason
            ("--id", "NOPE", "--to", "repairing"),                # unknown asset
        ]
        for extra in rejections:
            with self.subTest(extra=extra):
                result = self.invoke("asset", "status", *extra)
                self.assertEqual(result.returncode, 1)
                self.assertNotEqual(result.stderr, "")
        self.assertEqual(self.show(), before)
        # retired is terminal
        self.assertEqual(
            self.invoke(
                "asset", "status", "--id", "A001", "--to", "retired", "--reason", "报废"
            ).returncode,
            0,
        )
        for target in ["in_use", "repairing", "retired"]:
            result = self.invoke(
                "asset", "status", "--id", "A001", "--to", target, "--reason", "x"
            )
            self.assertEqual(result.returncode, 1, target)


class PlanCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "ledger.db"
        self.today = date.today()

    def invoke(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ, ASSET_LEDGER_DB=str(self.db_path))
        return subprocess.run(
            [sys.executable, "-m", "asset_ledger", *arguments],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

    def add_asset(self, asset_id: str) -> None:
        result = self.invoke(
            "asset", "add", "--id", asset_id, "--name", "设备",
            "--cost", "100", "--purchased-at", "2026-01-01",
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def add_plan(
        self,
        plan_id: str = "P001",
        asset_id: str = "A001",
        cycle_days: str = "30",
        first_due: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if first_due is None:
            first_due = (self.today - timedelta(days=10)).isoformat()
        return self.invoke(
            "plan", "add", "--id", plan_id, "--asset-id", asset_id,
            "--cycle-days", cycle_days, "--first-due", first_due,
        )

    def due(self, as_of: str) -> dict:
        result = self.invoke("plan", "due", "--as-of", as_of)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_plan_add_and_due_query(self) -> None:
        self.add_asset("A001")
        result = self.add_plan()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "plan P001 created")
        # first due 10 days ago, 30-day cycle -> next due 20 days from today
        expected = (self.today + timedelta(days=20)).isoformat()
        payload = self.due(self.today.isoformat())
        self.assertEqual(payload, {"as_of": self.today.isoformat(), "due": []})
        payload = self.due(expected)
        self.assertEqual(payload["as_of"], expected)
        self.assertEqual(
            payload["due"],
            [
                {
                    "plan_id": "P001",
                    "asset_id": "A001",
                    "due_date": expected,
                    "asset_status": "in_use",
                }
            ],
        )

    def test_due_sorting_and_retired_exclusion(self) -> None:
        self.add_asset("A001")
        self.add_asset("A002")
        self.add_asset("A003")
        # both due today; P002 must sort after P001 on the same date
        today_str = self.today.isoformat()
        self.assertEqual(self.add_plan("P002", "A001", "30", today_str).returncode, 0)
        self.assertEqual(self.add_plan("P001", "A002", "30", today_str).returncode, 0)
        self.assertEqual(self.add_plan("P003", "A003", "30", today_str).returncode, 0)
        result = self.invoke(
            "asset", "status", "--id", "A003", "--to", "retired", "--reason", "报废"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = self.due(self.today.isoformat())
        self.assertEqual(
            [entry["plan_id"] for entry in payload["due"]], ["P001", "P002"]
        )
        self.assertEqual(
            [entry["due_date"] for entry in payload["due"]],
            [self.today.isoformat()] * 2,
        )

    def test_plan_add_rejections_leave_no_trace(self) -> None:
        self.add_asset("A001")
        self.assertEqual(self.add_plan().returncode, 0)
        far_future = (self.today + timedelta(days=365)).isoformat()
        before = self.due(far_future)
        self.assertEqual([entry["plan_id"] for entry in before["due"]], ["P001"])
        future = (self.today + timedelta(days=1)).isoformat()
        rejections = [
            ("--id", "P002", "--asset-id", "NOPE", "--cycle-days", "30",
             "--first-due", "2026-01-01"),                       # unknown asset
            ("--id", "P001", "--asset-id", "A001", "--cycle-days", "30",
             "--first-due", "2026-01-01"),                       # duplicate plan id
            ("--id", "P002", "--asset-id", "A001", "--cycle-days", "0",
             "--first-due", "2026-01-01"),                       # zero cycle
            ("--id", "P002", "--asset-id", "A001", "--cycle-days", "-5",
             "--first-due", "2026-01-01"),                       # negative cycle
            ("--id", "P002", "--asset-id", "A001", "--cycle-days", "abc",
             "--first-due", "2026-01-01"),                       # non-integer cycle
            ("--id", "P002", "--asset-id", "A001", "--cycle-days", "30",
             "--first-due", "2026-13-01"),                       # invalid first-due
            ("--id", "P002", "--asset-id", "A001", "--cycle-days", "30",
             "--first-due", "not-a-date"),                       # malformed first-due
            ("--id", "P002", "--asset-id", "A001", "--cycle-days", "30",
             "--first-due", future),                             # future first-due
        ]
        for extra in rejections:
            with self.subTest(extra=extra):
                result = self.invoke("plan", "add", *extra)
                self.assertEqual(result.returncode, 1)
                self.assertTrue(result.stderr.startswith("error: "), result.stderr)
                self.assertEqual(result.stdout, "")
        self.assertEqual(self.due(far_future), before)

    def test_due_rejects_bad_as_of(self) -> None:
        for as_of in ["2026-13-01", "not-a-date", "2026-1-1"]:
            with self.subTest(as_of=as_of):
                result = self.invoke("plan", "due", "--as-of", as_of)
                self.assertEqual(result.returncode, 1)
                self.assertTrue(result.stderr.startswith("error: "), result.stderr)
                self.assertEqual(result.stdout, "")

    def test_plan_missing_arguments_is_an_error(self) -> None:
        result = self.invoke("plan", "add", "--id", "P001")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
