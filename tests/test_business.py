"""End-to-end checks for asset registration and location changes."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
ENV = {**os.environ, "PYTHONPATH": str(ROOT)}


class BusinessTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.db = self.tmp / "asset_ledger.db"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def invoke(self, *arguments: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "asset_ledger", *arguments],
            cwd=cwd or ROOT,
            capture_output=True,
            text=True,
            check=False,
            env=ENV,
        )

    def register(self, tag: str = "A001", **overrides: str) -> subprocess.CompletedProcess[str]:
        args = dict(
            tag=tag,
            name="笔记本电脑",
            category="电脑",
            **{"purchase-date": "2026-01-15"},
            location="北京-机房A",
        )
        args.update(overrides)
        cmd = ["register", "--db", str(self.db)]
        for key, value in args.items():
            cmd += [f"--{key}", value]
        return self.invoke(*cmd)

    def move(self, tag: str, location: str, date: str, note: str | None = None):
        cmd = [
            "move", "--db", str(self.db),
            "--tag", tag, "--location", location, "--date", date,
        ]
        if note is not None:
            cmd += ["--note", note]
        return self.invoke(*cmd)

    def test_register_creates_db_and_reports_assigned_id(self) -> None:
        self.assertFalse(self.db.exists())
        result = self.register()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertTrue(self.db.exists())
        self.assertIn("台账编号: 1", result.stdout)
        self.assertIn("资产标签: A001", result.stdout)
        self.assertIn("初始存放位置: 北京-机房A", result.stdout)

    def test_default_db_is_created_in_cwd_on_first_registration(self) -> None:
        result = self.invoke(
            "register",
            "--tag", "X1", "--name", "n", "--category", "c",
            "--purchase-date", "2026-01-01", "--location", "loc",
            cwd=self.tmp,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.tmp / "asset_ledger.db").exists())

    def test_read_commands_do_not_create_missing_db(self) -> None:
        for args in [("list",), ("show", "--tag", "X")]:
            result = self.invoke(*args, "--db", str(self.db))
            self.assertEqual(result.returncode, 0 if args[0] == "list" else 1)
            self.assertFalse(self.db.exists())

    def test_duplicate_tag_is_rejected(self) -> None:
        first = self.register()
        second = self.register(location="别处")
        self.assertEqual(first.returncode, 0)
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("资产标签重复", second.stderr)
        self.assertEqual(second.stdout, "")
        listing = self.invoke("list", "--db", str(self.db))
        self.assertEqual(listing.stdout.count("A001"), 1)

    def test_invalid_purchase_date_is_rejected(self) -> None:
        for bad in ["2026/01/15", "2026-13-01", "20260115", "not-a-date"]:
            with self.subTest(bad=bad):
                result = self.register(tag=f"B-{bad}", **{"purchase-date": bad})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("YYYY-MM-DD", result.stderr)
        self.assertFalse(self.db.exists())

    def test_missing_required_field_is_rejected(self) -> None:
        result = self.invoke(
            "register", "--db", str(self.db),
            "--tag", "A001", "--name", "n", "--category", "c",
            "--purchase-date", "2026-01-15",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--location", result.stderr)
        self.assertFalse(self.db.exists())

    def test_idempotent_request_id_returns_same_result(self) -> None:
        first = self.register(**{"request-id": "req-1"})
        second = self.register(**{"request-id": "req-1"})
        self.assertEqual(first.returncode, 0)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first.stdout, second.stdout)
        rows = self.invoke("list", "--db", str(self.db)).stdout
        self.assertEqual(rows.count("A001"), 1)

    def test_same_request_id_with_different_content_is_rejected(self) -> None:
        first = self.register(**{"request-id": "req-1"})
        clash = self.register(name="被篡改的名称", **{"request-id": "req-1"})
        self.assertEqual(first.returncode, 0)
        self.assertNotEqual(clash.returncode, 0)
        self.assertIn("请求标识", clash.stderr)
        # The original result is still returned afterwards.
        retry = self.register(**{"request-id": "req-1"})
        self.assertEqual(retry.returncode, 0)
        self.assertEqual(retry.stdout, first.stdout)

    def test_failed_attempt_does_not_poison_request_id(self) -> None:
        self.register(**{"request-id": "req-2"})
        failed = self.register(**{"request-id": "req-7"})  # duplicate tag
        self.assertNotEqual(failed.returncode, 0)
        ok = self.register(tag="A700", **{"request-id": "req-7"})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertIn("台账编号: 2", ok.stdout)
        self.assertEqual(
            self.register(tag="A700", **{"request-id": "req-7"}).stdout, ok.stdout
        )

    def test_move_appends_history_and_updates_current_location(self) -> None:
        self.register()
        first = self.move("A001", "上海-B座", "2026-03-01", note="部门搬迁")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("当前存放位置: 上海-B座", first.stdout)

        second = self.move("A001", "深圳-C座", "2026-05-01")
        self.assertEqual(second.returncode, 0, second.stderr)

        shown = self.invoke("show", "--db", str(self.db), "--tag", "A001")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertIn("当前存放位置: 深圳-C座", shown.stdout)
        self.assertIn("2026-03-01 北京-机房A -> 上海-B座", shown.stdout)
        self.assertIn("部门搬迁", shown.stdout)
        self.assertIn("2026-05-01 上海-B座 -> 深圳-C座", shown.stdout)
        self.assertLess(shown.stdout.index("2026-03-01"), shown.stdout.index("2026-05-01"))

    def test_show_without_history_says_so(self) -> None:
        self.register()
        shown = self.invoke("show", "--db", str(self.db), "--tag", "A001")
        self.assertEqual(shown.returncode, 0)
        self.assertIn("尚无变更记录", shown.stdout)

    def test_show_missing_asset_fails(self) -> None:
        self.register()
        result = self.invoke("show", "--db", str(self.db), "--tag", "GHOST")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("GHOST", result.stderr)

    def test_move_to_same_location_fails(self) -> None:
        self.register()
        result = self.move("A001", "北京-机房A", "2026-03-01")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("相同", result.stderr)
        shown = self.invoke("show", "--db", str(self.db), "--tag", "A001")
        self.assertIn("当前存放位置: 北京-机房A", shown.stdout)
        self.assertIn("尚无变更记录", shown.stdout)

    def test_move_earlier_than_latest_change_fails(self) -> None:
        self.register()
        self.move("A001", "上海-B座", "2026-05-01")
        result = self.move("A001", "广州", "2026-04-30")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("早于最近一次变更", result.stderr)
        shown = self.invoke("show", "--db", str(self.db), "--tag", "A001")
        self.assertIn("当前存放位置: 上海-B座", shown.stdout)
        self.assertNotIn("广州", shown.stdout)

    def test_move_same_date_as_latest_is_allowed(self) -> None:
        self.register()
        self.move("A001", "上海-B座", "2026-05-01")
        result = self.move("A001", "广州", "2026-05-01")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("当前存放位置: 广州", result.stdout)

    def test_move_unknown_asset_bad_date_fail(self) -> None:
        self.register()
        missing = self.move("NOPE", "广州", "2026-05-01")
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("目标资产不存在", missing.stderr)

        bad_date = self.move("A001", "广州", "2026-05-xx")
        self.assertNotEqual(bad_date.returncode, 0)
        self.assertIn("YYYY-MM-DD", bad_date.stderr)

    def test_list_orders_by_ledger_id_and_filters_category(self) -> None:
        self.register("A001", category="电脑")
        self.register("P002", name="投影仪", category="外设", location="成都")
        self.register("L003", name="笔记本2", category="电脑")
        all_rows = self.invoke("list", "--db", str(self.db))
        self.assertEqual(all_rows.returncode, 0)
        lines = [line for line in all_rows.stdout.splitlines() if line]
        self.assertEqual([line.split()[0] for line in lines], ["#1", "#2", "#3"])

        filtered = self.invoke("list", "--db", str(self.db), "--category", "外设")
        self.assertEqual(filtered.returncode, 0)
        filter_lines = [line for line in filtered.stdout.splitlines() if line]
        self.assertEqual(len(filter_lines), 1)
        self.assertIn("P002", filter_lines[0])

    def test_list_empty_ledger(self) -> None:
        result = self.invoke("list", "--db", str(self.db))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_db_option_works_before_and_after_subcommand(self) -> None:
        before = self.invoke(
            "--db", str(self.db), "register",
            "--tag", "Z9", "--name", "n", "--category", "c",
            "--purchase-date", "2026-01-01", "--location", "loc",
        )
        after = self.invoke(
            "register", "--db", str(self.db),
            "--tag", "Z8", "--name", "n", "--category", "c",
            "--purchase-date", "2026-01-01", "--location", "loc",
        )
        self.assertEqual(before.returncode, 0, before.stderr)
        self.assertEqual(after.returncode, 0, after.stderr)
        self.assertTrue(self.db.exists())


if __name__ == "__main__":
    unittest.main()
