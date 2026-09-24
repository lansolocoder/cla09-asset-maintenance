"""End-to-end checks for the asset ledger business commands."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class LedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "ledger.sqlite3"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def invoke(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        env = {**os.environ, "ASSET_LEDGER_DB": str(self.db_path)}
        return subprocess.run(
            [sys.executable, "-m", "asset_ledger", *arguments],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

    def register(
        self,
        code: str = "A001",
        *,
        name: str = "Laptop",
        category: str = "IT",
        purchase_date: str = "2026-01-15",
        amount: str = "9999.99",
        location_code: str = "BJ-01",
        location_name: str = "Beijing",
    ) -> subprocess.CompletedProcess[str]:
        return self.invoke(
            "register",
            "--asset-code", code,
            "--name", name,
            "--category", category,
            "--purchase-date", purchase_date,
            "--purchase-amount", amount,
            "--location-code", location_code,
            "--location-name", location_name,
        )

    def get_asset(self, code: str = "A001") -> dict[str, object]:
        result = self.invoke("get", "--asset-code", code)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    # ---- registration -----------------------------------------------------

    def test_register_success_is_stored_at_initial_location(self) -> None:
        result = self.register()
        self.assertEqual(result.returncode, 0, result.stderr)
        asset = json.loads(result.stdout)
        self.assertEqual(
            asset,
            {
                "asset_code": "A001",
                "name": "Laptop",
                "category": "IT",
                "purchase_date": "2026-01-15",
                "purchase_amount": 9999.99,
                "status": "在库",
                "current_location": {"code": "BJ-01", "name": "Beijing"},
            },
        )

    def test_register_amount_is_stored_exactly(self) -> None:
        self.assertEqual(self.register(amount="100.5").returncode, 0)
        self.assertEqual(self.get_asset()["purchase_amount"], 100.5)
        self.assertEqual(self.register(code="A002", amount="0.01").returncode, 0)
        self.assertEqual(self.get_asset("A002")["purchase_amount"], 0.01)

    def test_duplicate_asset_code_fails_without_changing_record(self) -> None:
        self.assertEqual(self.register(name="First").returncode, 0)
        result = self.register(name="Second")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicate-asset", result.stderr)
        self.assertEqual(self.get_asset()["name"], "First")

    def test_invalid_date_is_rejected(self) -> None:
        for bad in ("2026-02-30", "2026-13-01", "2026/01/15", "26-01-15"):
            with self.subTest(bad=bad):
                result = self.register(code=f"D{bad!r}", purchase_date=bad)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("invalid-date", result.stderr)

    def test_invalid_amount_is_rejected(self) -> None:
        for bad in ("0", "-1", "1.234", "abc", "1.", ".5", "100,00"):
            with self.subTest(bad=bad):
                result = self.register(code=f"A{bad!r}", amount=bad)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("invalid-amount", result.stderr)

    def test_missing_required_fields_are_rejected(self) -> None:
        base = {
            "code": "A001",
            "name": "Laptop",
            "category": "IT",
            "purchase_date": "2026-01-15",
            "amount": "10",
            "location_code": "L",
            "location_name": "",
        }
        for omit in ("name", "category", "purchase_date", "amount", "location_code"):
            kwargs = dict(base)
            kwargs[omit] = ""
            with self.subTest(omit=omit):
                result = self.register(**kwargs)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("missing-field", result.stderr)

    def test_failed_registration_persists_nothing(self) -> None:
        self.register()
        before = self.get_asset()
        self.register(code="A002", purchase_date="bad")
        self.register(code="A003", amount="0")
        result = self.invoke("get", "--asset-code", "A002")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.get_asset(), before)

    # ---- activation --------------------------------------------------------

    def activate(self, code: str = "A001", request_id: str = "R1",
                 effective_at: str = "2026-02-01T10:00:00") -> subprocess.CompletedProcess[str]:
        return self.invoke(
            "activate", "--asset-code", code,
            "--request-id", request_id, "--effective-at", effective_at,
        )

    def test_activate_stored_to_in_use(self) -> None:
        self.register()
        result = self.activate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "使用中")
        self.assertEqual(self.get_asset()["status"], "使用中")

    def test_activate_unknown_asset_fails(self) -> None:
        result = self.activate(code="NOPE")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("asset-not-found", result.stderr)

    def test_activate_before_purchase_date_fails(self) -> None:
        self.register()
        result = self.activate(effective_at="2026-01-14")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("effective-before-purchase", result.stderr)
        self.assertEqual(self.get_asset()["status"], "在库")

    def test_activate_on_purchase_date_is_allowed(self) -> None:
        self.register()
        result = self.activate(effective_at="2026-01-15")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_double_activate_is_rejected(self) -> None:
        self.register()
        self.assertEqual(self.activate().returncode, 0)
        result = self.activate(request_id="R2")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid-status-transition", result.stderr)

    def test_activate_replay_returns_same_result_without_new_event(self) -> None:
        self.register()
        first = self.activate()
        second = self.activate()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(second.stdout, first.stdout)
        history = json.loads(
            self.invoke("history", "--asset-code", "A001").stdout
        )
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["request_id"], "R1")

    def test_activate_same_request_id_different_payload_conflicts(self) -> None:
        self.register()
        self.assertEqual(self.activate(effective_at="2026-02-01T10:00:00").returncode, 0)
        result = self.activate(effective_at="2026-02-02T10:00:00")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("request-conflict", result.stderr)
        # State is untouched.
        self.assertEqual(
            len(json.loads(self.invoke("history", "--asset-code", "A001").stdout)), 1
        )

    # ---- moves -------------------------------------------------------------

    def move(self, code: str = "A001", request_id: str = "M1",
             effective_at: str = "2026-03-01T09:00:00",
             to_code: str = "SH-02", to_name: str = "Shanghai",
             reason: str = "relocate") -> subprocess.CompletedProcess[str]:
        return self.invoke(
            "move", "--asset-code", code,
            "--request-id", request_id, "--effective-at", effective_at,
            "--to-location-code", to_code, "--to-location-name", to_name,
            "--reason", reason,
        )

    def test_move_while_stored_is_allowed(self) -> None:
        self.register()
        result = self.move()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout)["current_location"],
            {"code": "SH-02", "name": "Shanghai"},
        )

    def test_move_unknown_asset_fails(self) -> None:
        result = self.move(code="NOPE")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("asset-not-found", result.stderr)

    def test_move_appends_history_and_updates_current_location_together(self) -> None:
        self.register()
        self.assertEqual(self.move().returncode, 0)
        asset = self.get_asset()
        self.assertEqual(asset["current_location"]["code"], "SH-02")
        history = json.loads(self.invoke("history", "--asset-code", "A001").stdout)
        self.assertEqual(len(history), 1)
        event = history[0]
        self.assertEqual(event["type"], "move")
        self.assertEqual(event["from_location"]["code"], "BJ-01")
        self.assertEqual(event["to_location"]["code"], "SH-02")
        self.assertEqual(event["reason"], "relocate")
        self.assertEqual(event["request_id"], "M1")
        self.assertIn("effective_at", event)
        self.assertIn("recorded_at", event)

    def test_late_move_does_not_change_current_location_or_rewrite_history(self) -> None:
        self.register()
        self.move(request_id="M1", effective_at="2026-03-01T09:00:00",
                  to_code="SH-02", to_name="Shanghai")
        late = self.move(request_id="M2", effective_at="2026-02-10T09:00:00",
                         to_code="SZ-03", to_name="Shenzhen", reason="repair")
        self.assertEqual(late.returncode, 0, late.stderr)
        # Current location still follows the latest *effective* move.
        self.assertEqual(self.get_asset()["current_location"]["code"], "SH-02")
        history = json.loads(self.invoke("history", "--asset-code", "A001").stdout)
        self.assertEqual([e["request_id"] for e in history], ["M2", "M1"])
        self.assertEqual([e["effective_at"] for e in history],
                         ["2026-02-10T09:00:00", "2026-03-01T09:00:00"])
        # recorded_at keeps true submission order: the late record was
        # submitted second even though it sorts first by effective time.
        self.assertGreater(history[0]["recorded_at"], history[1]["recorded_at"])
        # Existing record is untouched.
        self.assertEqual(history[1]["to_location"]["code"], "SH-02")

    def test_later_move_after_late_one_takes_over_current_location(self) -> None:
        self.register()
        self.move(request_id="M1", effective_at="2026-03-01T09:00:00", to_code="SH-02")
        self.move(request_id="M2", effective_at="2026-02-10T09:00:00", to_code="SZ-03")
        newest = self.move(request_id="M3", effective_at="2026-04-01T08:00:00",
                           to_code="GZ-04", to_name="Guangzhou", reason="audit")
        self.assertEqual(newest.returncode, 0, newest.stderr)
        self.assertEqual(self.get_asset()["current_location"]["code"], "GZ-04")

    def test_same_effective_time_resolves_by_submission_order(self) -> None:
        self.register()
        self.move(request_id="M1", effective_at="2026-03-01T09:00:00", to_code="SH-02")
        tie = self.move(request_id="M2", effective_at="2026-03-01T09:00:00",
                        to_code="CD-05", to_name="Chengdu", reason="tie")
        self.assertEqual(tie.returncode, 0, tie.stderr)
        self.assertEqual(self.get_asset()["current_location"]["code"], "CD-05")
        history = json.loads(self.invoke("history", "--asset-code", "A001").stdout)
        self.assertEqual([e["request_id"] for e in history], ["M1", "M2"])
        self.assertEqual(history[1]["from_location"]["code"], "SH-02")

    def test_move_replay_and_conflict(self) -> None:
        self.register()
        first = self.move(reason="relocate")
        replay = self.move(reason="relocate")
        self.assertEqual(replay.stdout, first.stdout)
        history = json.loads(self.invoke("history", "--asset-code", "A001").stdout)
        self.assertEqual(len(history), 1)
        conflict = self.move(reason="different-reason")
        self.assertNotEqual(conflict.returncode, 0)
        self.assertIn("request-conflict", conflict.stderr)
        self.assertEqual(
            len(json.loads(self.invoke("history", "--asset-code", "A001").stdout)), 1
        )

    def test_failed_move_changes_nothing(self) -> None:
        self.register()
        self.move()
        before = self.get_asset()
        # Missing reason -> validation failure.
        result = self.invoke(
            "move", "--asset-code", "A001", "--request-id", "MX",
            "--effective-at", "2026-04-01T00:00:00",
            "--to-location-code", "ZZ-99", "--reason", "",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing-field", result.stderr)
        self.assertEqual(self.get_asset(), before)
        history = json.loads(self.invoke("history", "--asset-code", "A001").stdout)
        self.assertEqual([e["request_id"] for e in history], ["M1"])

    # ---- persistence --------------------------------------------------------

    def test_records_survive_across_process_restarts(self) -> None:
        self.register()
        self.activate()
        self.move()
        history = json.loads(self.invoke("history", "--asset-code", "A001").stdout)
        self.assertEqual(len(history), 2)
        asset = self.get_asset()
        self.assertEqual(asset["status"], "使用中")
        self.assertEqual(asset["current_location"]["code"], "SH-02")

    def test_history_unknown_asset_fails(self) -> None:
        result = self.invoke("history", "--asset-code", "GHOST")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("asset-not-found", result.stderr)

    def test_every_success_is_a_single_json_line(self) -> None:
        self.register()
        for result in (
            self.invoke("get", "--asset-code", "A001"),
            self.invoke("history", "--asset-code", "A001"),
            self.activate(),
        ):
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.count("\n"), 1)


if __name__ == "__main__":
    unittest.main()
