"""资产登记、位置变更、查询与持久化的端到端测试（通过子进程调用 CLI）。"""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class LedgerCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "ledger.db"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def invoke(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "asset_ledger", "--db", str(self.db), *arguments],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def register(self, asset_id: str = "A001", **overrides: str) -> subprocess.CompletedProcess[str]:
        values = {
            "id": asset_id,
            "name": "笔记本电脑",
            "category": "IT设备",
            "purchase-date": "2026-01-15",
            "cost": "9999.50",
            "location": "北京办公室",
        }
        values.update(overrides)
        args: list[str] = []
        for key, value in values.items():
            args.extend([f"--{key}", value])
        return self.invoke("register", *args)

    # ---- 登记 ----------------------------------------------------------

    def test_register_success_output(self) -> None:
        result = self.register()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("资产编号: A001", result.stdout)
        self.assertIn("状态: 在用", result.stdout)
        self.assertIn("初始存放位置: 北京办公室", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_duplicate_id_fails_and_leaves_no_trace(self) -> None:
        first = self.register(location="北京办公室", cost="9999.50")
        self.assertEqual(first.returncode, 0, first.stderr)

        second = self.register(location="上海仓库", cost="1.00", name="另一台")
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("已存在", second.stderr)
        self.assertEqual(second.stdout, "")

        shown = self.invoke("show", "--id", "A001")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertIn("北京办公室", shown.stdout)
        self.assertNotIn("上海仓库", shown.stdout)
        self.assertIn("9999.50", shown.stdout)
        self.assertNotIn("1.00", shown.stdout)
        self.assertIn("笔记本电脑", shown.stdout)
        self.assertNotIn("另一台", shown.stdout)
        # 初始位置仍是唯一一条历史。
        self.assertEqual(shown.stdout.count("[初始]"), 1)
        self.assertNotIn("[变更]", shown.stdout)

    def test_missing_required_field_writes_nothing(self) -> None:
        for field, label in [
            ("name", "名称"),
            ("category", "类别"),
            ("location", "存放位置"),
        ]:
            with self.subTest(field=field):
                result = self.register("A002", **{field: "   "})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(label, result.stderr)
                self.assertFalse(
                    self.invoke("show", "--id", "A002").returncode == 0,
                    "失败登记不得创建资产",
                )

    def test_invalid_date_formats_rejected(self) -> None:
        for bad in ["2026/01/15", "2026-1-15", "20260115", "2026-13-01", "2026-02-30"]:
            with self.subTest(bad=bad):
                result = self.register("B001", **{"purchase-date": bad})
                self.assertNotEqual(result.returncode, 0, bad)
                self.assertIn("日期", result.stderr)

    def test_invalid_cost_rejected(self) -> None:
        for bad in ["-1", "abc", "1.2.3", "1e3", "", "9,999"]:
            with self.subTest(bad=bad):
                result = self.register("B002", cost=bad)
                self.assertNotEqual(result.returncode, 0, repr(bad))
                self.assertIn("金额", result.stderr)

    def test_zero_and_decimal_cost_allowed(self) -> None:
        result = self.register("C001", cost="0")
        self.assertEqual(result.returncode, 0, result.stderr)
        result2 = self.register("C002", cost="0.0001")
        self.assertEqual(result2.returncode, 0, result2.stderr)

    def test_date_and_cost_stored_verbatim(self) -> None:
        result = self.register("D001", **{"purchase-date": "2026-01-15", "cost": "12345.678901"})
        self.assertEqual(result.returncode, 0, result.stderr)
        shown = self.invoke("show", "--id", "D001")
        self.assertIn("购置日期: 2026-01-15", shown.stdout)
        self.assertIn("购置金额(元): 12345.678901", shown.stdout)

    # ---- 位置变更 ------------------------------------------------------

    def test_move_success_outputs_current_location(self) -> None:
        self.register()
        result = self.invoke("move", "--id", "A001", "--location", "上海分部", "--date", "2026-03-01")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("资产编号: A001", result.stdout)
        self.assertIn("当前存放位置: 上海分部", result.stdout)

    def test_move_unknown_asset_does_not_create_anything(self) -> None:
        result = self.invoke("move", "--id", "GHOST", "--location", "x", "--date", "2026-03-01")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("未登记", result.stderr)

        shown = self.invoke("show", "--id", "GHOST")
        self.assertNotEqual(shown.returncode, 0)
        self.assertIn("未登记", shown.stderr)

    def test_move_date_before_purchase_rejected(self) -> None:
        self.register()
        result = self.invoke("move", "--id", "A001", "--location", "x", "--date", "2025-12-31")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("购置日期", result.stderr)

        shown = self.invoke("show", "--id", "A001")
        self.assertNotIn("[变更]", shown.stdout)
        self.assertIn("当前存放位置: 北京办公室", shown.stdout)

    def test_move_date_before_last_record_rejected(self) -> None:
        self.register()
        ok = self.invoke("move", "--id", "A001", "--location", "上海分部", "--date", "2026-03-01")
        self.assertEqual(ok.returncode, 0, ok.stderr)

        bad = self.invoke("move", "--id", "A001", "--location", "广州", "--date", "2026-02-01")
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("最后一条位置记录日期", bad.stderr)

        shown = self.invoke("show", "--id", "A001")
        self.assertEqual(shown.stdout.count("[变更]"), 1)
        self.assertIn("当前存放位置: 上海分部", shown.stdout)
        self.assertNotIn("广州", shown.stdout)

    def test_same_day_moves_keep_entry_order(self) -> None:
        self.register()
        for location in ["上海分部", "广州仓库", "成都机房"]:
            result = self.invoke(
                "move", "--id", "A001", "--location", location, "--date", "2026-03-01"
            )
            self.assertEqual(result.returncode, 0, result.stderr)

        shown = self.invoke("show", "--id", "A001")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        lines = [line for line in shown.stdout.splitlines() if "[变更]" in line]
        self.assertEqual(len(lines), 3)
        self.assertIn("上海分部", lines[0])
        self.assertIn("广州仓库", lines[1])
        self.assertIn("成都机房", lines[2])
        self.assertIn("当前存放位置: 成都机房", shown.stdout)

    def test_move_requires_valid_date_and_location(self) -> None:
        self.register()
        bad_date = self.invoke(
            "move", "--id", "A001", "--location", "x", "--date", "not-a-date"
        )
        self.assertNotEqual(bad_date.returncode, 0)
        empty_location = self.invoke(
            "move", "--id", "A001", "--location", "  ", "--date", "2026-03-01"
        )
        self.assertNotEqual(empty_location.returncode, 0)

    # ---- 查询 ----------------------------------------------------------

    def test_show_full_history(self) -> None:
        self.register()
        self.invoke("move", "--id", "A001", "--location", "上海分部", "--date", "2026-03-01")
        result = self.invoke("show", "--id", "A001")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("资产编号: A001", result.stdout)
        self.assertIn("名称: 笔记本电脑", result.stdout)
        self.assertIn("类别: IT设备", result.stdout)
        self.assertIn("购置日期: 2026-01-15", result.stdout)
        self.assertIn("购置金额(元): 9999.50", result.stdout)
        self.assertIn("状态: 在用", result.stdout)
        self.assertIn("当前存放位置: 上海分部", result.stdout)
        self.assertIn("[初始] 北京办公室", result.stdout)
        self.assertIn("[变更] 上海分部", result.stdout)

    def test_show_unknown_asset_fails_on_stderr(self) -> None:
        result = self.invoke("show", "--id", "MISSING")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("未登记", result.stderr)
        self.assertEqual(result.stdout, "")

    # ---- 持久化与失败可重试 --------------------------------------------

    def test_data_persists_across_invocations(self) -> None:
        self.register()
        # 全新进程再次查询，数据仍在。
        shown = self.invoke("show", "--id", "A001")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertIn("状态: 在用", shown.stdout)

    def test_failure_then_retry_is_equivalent_to_clean_success(self) -> None:
        # 先以非法输入失败两次，再用合法输入登记。
        bad1 = self.register("E001", cost="not-money")
        bad2 = self.register("E001", **{"purchase-date": "2026-13-01"})
        self.assertNotEqual(bad1.returncode, 0)
        self.assertNotEqual(bad2.returncode, 0)

        good = self.register(
            "E001",
            name="投影仪",
            category="会议设备",
            **{"purchase-date": "2026-04-01"},
            cost="3500.00",
            location="总部会议室",
        )
        self.assertEqual(good.returncode, 0, good.stderr)

        shown = self.invoke("show", "--id", "E001")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertIn("名称: 投影仪", shown.stdout)
        self.assertIn("类别: 会议设备", shown.stdout)
        self.assertIn("购置日期: 2026-04-01", shown.stdout)
        self.assertIn("购置金额(元): 3500.00", shown.stdout)
        self.assertIn("当前存放位置: 总部会议室", shown.stdout)
        self.assertEqual(shown.stdout.count("[初始]"), 1)
        self.assertNotIn("[变更]", shown.stdout)


if __name__ == "__main__":
    unittest.main()
