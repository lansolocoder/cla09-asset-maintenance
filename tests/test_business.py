"""资产登记、位置变更、查询与持久化的端到端测试（通过子进程调用 CLI）。"""

from pathlib import Path
import sqlite3
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

    # ---- 报废登记 ------------------------------------------------------

    def scrap(
        self,
        asset_id: str = "A001",
        scrap_date: str = "2026-09-01",
        reason: str = "主板损坏无法修复",
        request_id: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        args = ["scrap", "--id", asset_id, "--date", scrap_date, "--reason", reason]
        if request_id is not None:
            args.extend(["--request-id", request_id])
        return self.invoke(*args)

    def scrap_records(self, asset_id: str = "A001") -> list[tuple]:
        with sqlite3.connect(self.db) as conn:
            return conn.execute(
                "SELECT asset_id, scrap_date, reason FROM scrap_records "
                "WHERE asset_id = ? ORDER BY id",
                (asset_id,),
            ).fetchall()

    def test_scrap_success_output(self) -> None:
        self.register()
        result = self.scrap()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("资产编号: A001", result.stdout)
        self.assertIn("状态: 已报废", result.stdout)
        self.assertIn("报废日期: 2026-09-01", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_scrap_appends_one_record_and_updates_status(self) -> None:
        self.register()
        self.assertEqual(self.scrap().returncode, 0)
        self.assertEqual(
            self.scrap_records(), [("A001", "2026-09-01", "主板损坏无法修复")]
        )
        shown = self.invoke("show", "--id", "A001")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertIn("状态: 已报废", shown.stdout)
        # 位置历史不被报废改动。
        self.assertEqual(shown.stdout.count("[初始]"), 1)
        self.assertNotIn("[变更]", shown.stdout)

    def test_scrap_unknown_asset_writes_nothing(self) -> None:
        result = self.scrap("GHOST")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("未登记", result.stderr)
        self.assertEqual(result.stdout, "")
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM scrap_records").fetchone()[0], 0
            )

    def test_scrap_twice_without_request_id_fails(self) -> None:
        self.register()
        first = self.scrap()
        self.assertEqual(first.returncode, 0, first.stderr)
        second = self.scrap(reason="再次报废")
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("已报废", second.stderr)
        self.assertEqual(second.stdout, "")
        # 只有首次一条报废记录。
        self.assertEqual(len(self.scrap_records()), 1)

    def test_scrap_rejects_bad_date_and_empty_reason(self) -> None:
        self.register()
        for bad in ["2026/09/01", "2026-9-1", "not-a-date", "2026-02-30"]:
            with self.subTest(bad=bad):
                result = self.scrap(scrap_date=bad)
                self.assertNotEqual(result.returncode, 0, bad)
                self.assertIn("日期", result.stderr)
        result = self.scrap(reason="   ")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("报废原因", result.stderr)
        shown = self.invoke("show", "--id", "A001")
        self.assertIn("状态: 在用", shown.stdout)
        self.assertEqual(self.scrap_records(), [])

    def test_scrap_date_before_last_location_rejected(self) -> None:
        self.register()
        moved = self.invoke(
            "move", "--id", "A001", "--location", "上海分部", "--date", "2026-03-01"
        )
        self.assertEqual(moved.returncode, 0, moved.stderr)
        result = self.scrap(scrap_date="2026-02-28")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("最后一条位置记录日期", result.stderr)
        self.assertEqual(result.stdout, "")
        shown = self.invoke("show", "--id", "A001")
        self.assertIn("状态: 在用", shown.stdout)
        self.assertEqual(self.scrap_records(), [])

        # 与最后一条位置记录同日允许报废。
        same_day = self.scrap(scrap_date="2026-03-01")
        self.assertEqual(same_day.returncode, 0, same_day.stderr)

    def test_scrapped_asset_blocks_move_and_plan_register(self) -> None:
        self.register()
        self.assertEqual(self.scrap().returncode, 0)

        moved = self.invoke(
            "move", "--id", "A001", "--location", "上海分部", "--date", "2026-10-01"
        )
        self.assertNotEqual(moved.returncode, 0)
        self.assertIn("已报废", moved.stderr)
        self.assertEqual(moved.stdout, "")

        planned = self.invoke(
            "plan-register",
            "--plan-id", "P001", "--asset-id", "A001",
            "--type", "保养",
            "--first-due-date", "2026-10-01", "--period-days", "30",
        )
        self.assertNotEqual(planned.returncode, 0)
        self.assertIn("已报废", planned.stderr)
        self.assertEqual(planned.stdout, "")

        shown = self.invoke("show", "--id", "A001")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertIn("状态: 已报废", shown.stdout)
        self.assertNotIn("上海分部", shown.stdout)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM maintenance_plans").fetchone()[0], 0
            )

    def test_scrap_idempotent_same_request_id_replays_success(self) -> None:
        self.register()
        first = self.scrap(request_id="REQ-1")
        self.assertEqual(first.returncode, 0, first.stderr)
        second = self.scrap(request_id="REQ-1")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(second.stdout, first.stdout)
        self.assertEqual(len(self.scrap_records()), 1)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM scrap_requests WHERE request_id = ?",
                    ("REQ-1",),
                ).fetchone()[0],
                1,
            )

    def test_scrap_same_request_id_different_payload_fails(self) -> None:
        self.register()
        self.assertEqual(self.scrap(request_id="REQ-2").returncode, 0)

        for field, kwargs in [
            ("asset", {"asset_id": "A002"}),
            ("date", {"scrap_date": "2026-09-02"}),
            ("reason", {"reason": "其他原因"}),
        ]:
            with self.subTest(field=field):
                if field == "asset":
                    self.register(
                        "A002", **{"purchase-date": "2026-01-15"}, location="北京办公室"
                    )
                result = self.scrap(request_id="REQ-2", **kwargs)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("请求编号", result.stderr)
                self.assertEqual(result.stdout, "")

        # 原有报废记录与状态不变。
        self.assertEqual(
            self.scrap_records(), [("A001", "2026-09-01", "主板损坏无法修复")]
        )
        shown = self.invoke("show", "--id", "A001")
        self.assertIn("状态: 已报废", shown.stdout)

    def test_scrap_request_id_must_be_non_empty(self) -> None:
        self.register()
        result = self.scrap(request_id="   ")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("请求编号", result.stderr)
        self.assertEqual(self.scrap_records(), [])

    def test_failed_scrap_then_retry_is_equivalent_to_clean_success(self) -> None:
        self.register()
        bad1 = self.scrap("GHOST", request_id="REQ-3")
        bad2 = self.scrap(scrap_date="bad-date", request_id="REQ-3")
        bad3 = self.scrap(reason=" ", request_id="REQ-3")
        self.assertNotEqual(bad1.returncode, 0)
        self.assertNotEqual(bad2.returncode, 0)
        self.assertNotEqual(bad3.returncode, 0)

        good = self.scrap(request_id="REQ-3")
        self.assertEqual(good.returncode, 0, good.stderr)
        self.assertEqual(len(self.scrap_records()), 1)
        # 幂等重试仍成功，且幂等请求表只有一条。
        replay = self.scrap(request_id="REQ-3")
        self.assertEqual(replay.returncode, 0, replay.stderr)
        self.assertEqual(len(self.scrap_records()), 1)

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

    # ---- 维保计划登记 --------------------------------------------------

    def plan_register(
        self,
        plan_id: str = "P001",
        asset_id: str = "A001",
        plan_type: str = "常规保养",
        first_due_date: str = "2026-06-30",
        period_days: str = "90",
    ) -> subprocess.CompletedProcess[str]:
        return self.invoke(
            "plan-register",
            "--plan-id", plan_id,
            "--asset-id", asset_id,
            "--type", plan_type,
            "--first-due-date", first_due_date,
            "--period-days", period_days,
        )

    def test_plan_register_success_output(self) -> None:
        self.register()
        result = self.plan_register()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("计划编号: P001", result.stdout)
        self.assertIn("资产编号: A001", result.stdout)
        self.assertIn("首次到期日期: 2026-06-30", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_duplicate_plan_id_fails_and_keeps_original(self) -> None:
        self.register()
        first = self.plan_register(
            "P001", plan_type="常规保养", first_due_date="2026-06-30", period_days="90"
        )
        self.assertEqual(first.returncode, 0, first.stderr)

        second = self.plan_register(
            "P001", plan_type="年度大修", first_due_date="2027-01-01", period_days="365"
        )
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("已存在", second.stderr)
        self.assertEqual(second.stdout, "")

        listed = self.invoke("plan-list", "--asset-id", "A001")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertEqual(listed.stdout.count("计划编号: P001"), 1)
        self.assertIn("常规保养", listed.stdout)
        self.assertIn("2026-06-30", listed.stdout)
        self.assertIn("周期天数: 90", listed.stdout)
        self.assertNotIn("年度大修", listed.stdout)
        self.assertNotIn("2027-01-01", listed.stdout)
        self.assertNotIn("365", listed.stdout)

    def test_plan_register_unknown_asset_does_not_create_asset(self) -> None:
        result = self.plan_register("P002", asset_id="GHOST")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("未登记", result.stderr)
        self.assertEqual(result.stdout, "")

        shown = self.invoke("show", "--id", "GHOST")
        self.assertNotEqual(shown.returncode, 0)
        listed = self.invoke("plan-list", "--asset-id", "GHOST")
        self.assertNotEqual(listed.returncode, 0)

    def test_plan_register_rejects_bad_dates(self) -> None:
        self.register()
        for bad in ["2026/06/30", "2026-6-30", "20260630", "2026-13-01", "2026-02-30"]:
            with self.subTest(bad=bad):
                result = self.plan_register("PBAD", first_due_date=bad)
                self.assertNotEqual(result.returncode, 0, bad)
                self.assertIn("日期", result.stderr)
        listed = self.invoke("plan-list", "--asset-id", "A001")
        self.assertNotIn("PBAD", listed.stdout)

    def test_plan_register_rejects_non_positive_integer_period(self) -> None:
        self.register()
        for bad in ["0", "-3", "1.5", "abc", "", "1e2", "+1"]:
            with self.subTest(bad=bad):
                result = self.invoke(
                    "plan-register",
                    "--plan-id", "PPER",
                    "--asset-id", "A001",
                    "--type", "保养",
                    "--first-due-date", "2026-06-30",
                    "--period-days", bad,
                )
                self.assertNotEqual(result.returncode, 0, repr(bad))
                self.assertIn("周期天数", result.stderr)
        listed = self.invoke("plan-list", "--asset-id", "A001")
        self.assertNotIn("PPER", listed.stdout)

    def test_multiple_plans_same_asset_are_append_only(self) -> None:
        self.register()
        self.assertEqual(self.plan_register("P001").returncode, 0)
        second = self.plan_register("P002", plan_type="年度巡检", period_days="365")
        self.assertEqual(second.returncode, 0, second.stderr)

        listed = self.invoke("plan-list", "--asset-id", "A001")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertEqual(listed.stdout.count("计划编号:"), 2)

    # ---- 按资产列出维保计划 --------------------------------------------

    def test_plan_list_ordering_by_date_then_entry_order(self) -> None:
        self.register()
        # 先录入晚到期的计划，再录入早到期的计划，验证按日期重排。
        self.plan_register("P002", plan_type="年度巡检", first_due_date="2026-12-31", period_days="365")
        self.plan_register("P001", plan_type="常规保养", first_due_date="2026-06-30", period_days="90")
        # 与 P001 同日但更晚录入，应排在 P001 之后。
        self.plan_register("P003", plan_type="安全检查", first_due_date="2026-06-30", period_days="30")

        listed = self.invoke("plan-list", "--asset-id", "A001")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        blocks = [
            line for line in listed.stdout.splitlines() if "计划编号:" in line
        ]
        self.assertEqual(blocks, ["  计划编号: P001", "  计划编号: P003", "  计划编号: P002"])
        self.assertIn("维保类型: 常规保养", listed.stdout)
        self.assertIn("首次到期日期: 2026-06-30", listed.stdout)
        self.assertIn("周期天数: 90", listed.stdout)

    def test_plan_list_unknown_asset_fails_on_stderr(self) -> None:
        result = self.invoke("plan-list", "--asset-id", "MISSING")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("未登记", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_plan_list_empty_for_asset_without_plans(self) -> None:
        self.register()
        result = self.invoke("plan-list", "--asset-id", "A001")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("无记录", result.stdout)

    # ---- 到期待办清单 --------------------------------------------------

    def test_dues_includes_due_plans_with_next_date_and_location(self) -> None:
        self.register(location="北京办公室")
        self.invoke("move", "--id", "A001", "--location", "上海分部", "--date", "2026-03-01")
        # 首次 2026-03-02，周期 30 天；截止 2026-04-01，下一次到期恰为 2026-04-01。
        self.plan_register(
            "P001", plan_type="常规保养", first_due_date="2026-03-02", period_days="30"
        )
        result = self.invoke("dues", "--cutoff-date", "2026-04-01")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("计划编号: P001", result.stdout)
        self.assertIn("资产编号: A001", result.stdout)
        self.assertIn("维保类型: 常规保养", result.stdout)
        self.assertIn("下一次到期日期: 2026-04-01", result.stdout)
        self.assertIn("当前存放位置: 上海分部", result.stdout)

    def test_dues_excludes_not_yet_due_plans(self) -> None:
        self.register()
        self.plan_register(
            "P001", plan_type="常规保养", first_due_date="2026-06-30", period_days="90"
        )
        result = self.invoke("dues", "--cutoff-date", "2026-06-29")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("P001", result.stdout)
        self.assertIn("无到期计划", result.stdout)

    def test_dues_inclusive_on_cutoff_date(self) -> None:
        self.register()
        self.plan_register(
            "P001", plan_type="常规保养", first_due_date="2026-06-30", period_days="90"
        )
        result = self.invoke("dues", "--cutoff-date", "2026-06-30")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("下一次到期日期: 2026-06-30", result.stdout)

    def test_dues_picks_latest_occurrence_before_cutoff(self) -> None:
        self.register()
        # 首次 2026-01-01，周期 30 天：01-01, 01-31, 03-02, 04-01 ...
        self.plan_register(
            "P001", plan_type="保养", first_due_date="2026-01-01", period_days="30"
        )
        result = self.invoke("dues", "--cutoff-date", "2026-03-15")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("下一次到期日期: 2026-03-02", result.stdout)

    def test_dues_ordering_by_due_date_then_entry_order(self) -> None:
        self.register("A001", location="北京办公室")
        self.register("A002", location="上海仓库")
        # P002 录入在前但到期更晚；P001 录入在后但到期更早。
        self.plan_register(
            "P002", asset_id="A002", plan_type="年检",
            first_due_date="2026-01-10", period_days="40",
        )
        self.plan_register(
            "P001", asset_id="A001", plan_type="月检",
            first_due_date="2026-01-01", period_days="30",
        )
        # P003 与 P001 下次到期同为 2026-01-01（周期 20 天，下一期 01-21 超出），但录入更晚。
        self.plan_register(
            "P003", asset_id="A001", plan_type="巡检",
            first_due_date="2026-01-01", period_days="20",
        )

        result = self.invoke("dues", "--cutoff-date", "2026-01-20")
        self.assertEqual(result.returncode, 0, result.stderr)
        # P001 与 P003 下次到期均为 01-01，按录入顺序 P001 在前；P002 为 01-10。
        plan_lines = [
            line for line in result.stdout.splitlines() if "计划编号:" in line
        ]
        self.assertEqual(
            plan_lines,
            ["  计划编号: P001", "  计划编号: P003", "  计划编号: P002"],
        )

    def test_dues_rejects_bad_cutoff_date(self) -> None:
        for bad in ["2026/01/01", "2026-13-01", "not-a-date"]:
            with self.subTest(bad=bad):
                result = self.invoke("dues", "--cutoff-date", bad)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("日期", result.stderr)

    # ---- 维保计划失败可重试 --------------------------------------------

    def test_plan_failure_then_retry_is_equivalent_to_clean_success(self) -> None:
        self.register()
        bad1 = self.plan_register("F001", asset_id="GHOST")
        bad2 = self.invoke(
            "plan-register",
            "--plan-id", "F001", "--asset-id", "A001",
            "--type", "保养",
            "--first-due-date", "2026-06-30", "--period-days", "0",
        )
        bad3 = self.plan_register("F001", first_due_date="2026-02-30")
        self.assertNotEqual(bad1.returncode, 0)
        self.assertNotEqual(bad2.returncode, 0)
        self.assertNotEqual(bad3.returncode, 0)

        good = self.plan_register(
            "F001", plan_type="常规保养", first_due_date="2026-06-30", period_days="90"
        )
        self.assertEqual(good.returncode, 0, good.stderr)

        listed = self.invoke("plan-list", "--asset-id", "A001")
        self.assertEqual(listed.stdout.count("计划编号: F001"), 1)
        self.assertIn("常规保养", listed.stdout)
        self.assertIn("周期天数: 90", listed.stdout)


if __name__ == "__main__":
    unittest.main()
