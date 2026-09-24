"""Checks for the documented command-line entry point."""

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


class BusinessCommandTests(unittest.TestCase):
    """登记、位置变更与查询，全部落到临时目录里的独立数据文件。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "ledger.db"
        self.env = {**os.environ, "ASSET_LEDGER_DB": str(self.db_path)}

    def invoke(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "asset_ledger", *arguments],
            cwd=ROOT,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )

    def register(self, asset_id: str = "DEV-001", **overrides: str) -> subprocess.CompletedProcess[str]:
        fields = {
            "--asset-id": asset_id,
            "--name": "示波器",
            "--category": "测量仪器",
            "--purchase-date": "2024-01-15",
            "--purchase-amount": "12999.50",
            "--location": "一号仓库",
            **{f"--{k.replace('_', '-')}": v for k, v in overrides.items()},
        }
        arguments = ["register"]
        for key, value in fields.items():
            arguments += [key, value]
        return self.invoke(*arguments)

    def test_register_success(self) -> None:
        result = self.register()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DEV-001", result.stdout)
        self.assertIn("在用", result.stdout)
        self.assertIn("一号仓库", result.stdout)
        self.assertEqual(result.stderr, "")
        self.assertTrue(self.db_path.exists())

    def test_register_duplicate_fails_and_keeps_original(self) -> None:
        self.assertEqual(self.register().returncode, 0)
        again = self.register(name="另一台设备")
        self.assertNotEqual(again.returncode, 0)
        self.assertIn("DEV-001", again.stderr)
        shown = self.invoke("show", "--asset-id", "DEV-001")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertIn("示波器", shown.stdout)
        self.assertNotIn("另一台设备", shown.stdout)

    def test_register_rejects_invalid_input_without_writing(self) -> None:
        bad_calls = [
            ("--purchase-date", "2024/01/15"),
            ("--purchase-date", "2024-13-01"),
            ("--purchase-amount", "-1"),
            ("--purchase-amount", "abc"),
            ("--name", ""),
        ]
        for key, value in bad_calls:
            with self.subTest(key=key, value=value):
                result = self.register(**{key.lstrip("-").replace("-", "_"): value})
                self.assertNotEqual(result.returncode, 0)
                self.assertNotEqual(result.stderr, "")
        self.assertFalse(self.db_path.exists())

    def test_register_missing_field_is_an_error(self) -> None:
        result = self.invoke("register", "--asset-id", "DEV-002")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotEqual(result.stderr, "")
        self.assertFalse(self.db_path.exists())

    def test_relocate_and_show_history(self) -> None:
        self.assertEqual(self.register().returncode, 0)
        moved = self.invoke(
            "relocate", "--asset-id", "DEV-001",
            "--location", "二楼办公室", "--change-date", "2024-03-01",
        )
        self.assertEqual(moved.returncode, 0, moved.stderr)
        self.assertIn("二楼办公室", moved.stdout)

        shown = self.invoke("show", "--asset-id", "DEV-001")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertIn("状态: 在用", shown.stdout)
        self.assertIn("当前存放位置: 二楼办公室", shown.stdout)
        self.assertIn("2024-01-15 一号仓库", shown.stdout)
        self.assertIn("2024-03-01 二楼办公室", shown.stdout)
        self.assertLess(
            shown.stdout.index("2024-01-15 一号仓库"),
            shown.stdout.index("2024-03-01 二楼办公室"),
        )

    def test_relocate_unknown_asset_fails(self) -> None:
        result = self.invoke(
            "relocate", "--asset-id", "GHOST",
            "--location", "任意位置", "--change-date", "2024-03-01",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("GHOST", result.stderr)
        shown = self.invoke("show", "--asset-id", "GHOST")
        self.assertNotEqual(shown.returncode, 0)

    def test_relocate_rejects_date_before_purchase(self) -> None:
        self.assertEqual(self.register().returncode, 0)
        result = self.invoke(
            "relocate", "--asset-id", "DEV-001",
            "--location", "二楼办公室", "--change-date", "2023-12-31",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("2023-12-31", result.stderr)
        shown = self.invoke("show", "--asset-id", "DEV-001")
        self.assertIn("当前存放位置: 一号仓库", shown.stdout)

    def test_relocate_rejects_date_before_last_record(self) -> None:
        self.assertEqual(self.register().returncode, 0)
        self.assertEqual(
            self.invoke(
                "relocate", "--asset-id", "DEV-001",
                "--location", "二楼办公室", "--change-date", "2024-03-01",
            ).returncode,
            0,
        )
        result = self.invoke(
            "relocate", "--asset-id", "DEV-001",
            "--location", "地下室", "--change-date", "2024-02-01",
        )
        self.assertNotEqual(result.returncode, 0)
        shown = self.invoke("show", "--asset-id", "DEV-001")
        self.assertIn("当前存放位置: 二楼办公室", shown.stdout)
        self.assertNotIn("地下室", shown.stdout)

    def test_show_unknown_asset_fails(self) -> None:
        result = self.invoke("show", "--asset-id", "NOPE")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("NOPE", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_amount_and_date_are_kept_verbatim(self) -> None:
        self.assertEqual(
            self.register("DEV-009", purchase_amount="0.10").returncode, 0
        )
        shown = self.invoke("show", "--asset-id", "DEV-009")
        self.assertIn("购置金额: 0.10 元", shown.stdout)
        self.assertIn("购置日期: 2024-01-15", shown.stdout)


if __name__ == "__main__":
    unittest.main()
