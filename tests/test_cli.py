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


if __name__ == "__main__":
    unittest.main()
