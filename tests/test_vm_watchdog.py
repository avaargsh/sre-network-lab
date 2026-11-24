"""LAB-03D watchdog path must prove timer-driven recovery, not manual restore."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

VM = Path(__file__).resolve().parents[1] / "environments" / "vm"
sys.path.insert(0, str(VM))
spec = importlib.util.spec_from_file_location("vm_mtu_drill_watchdog", VM / "vm_mtu_drill.py")
drill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(drill)


class WatchdogContracts(unittest.TestCase):
    def test_watchdog_restore_requires_expired_timer_and_baseline_mtu(self):
        receipt = {
            "status": "NO_ACTIVE_LEASE",
            "run_id": "watchdog-run01",
            "interface": "enp0s9",
            "mtu": 1500,
            "timer_active": False,
        }
        drill.require_watchdog_restored(receipt, "watchdog-run01")
        for change in (
            {"status": "ARMED"},
            {"run_id": "other-run"},
            {"mtu": 1200},
            {"timer_active": True},
            {"timer_active": None},
        ):
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    drill.require_watchdog_restored({**receipt, **change}, "watchdog-run01")

    def test_wait_helper_uses_monotonic_elapsed_time_and_full_lease_window(self):
        clock = {"now": 10.0}
        sleeps = []

        def monotonic():
            return clock["now"]

        def sleep(seconds):
            sleeps.append(seconds)
            clock["now"] += seconds

        elapsed = drill.wait_for_watchdog(sleep_fn=sleep, monotonic_fn=monotonic)
        self.assertGreaterEqual(elapsed, drill.WATCHDOG_WAIT_SECONDS)
        self.assertEqual(sleeps, [drill.WATCHDOG_WAIT_SECONDS])

    def test_watchdog_drill_does_not_issue_manual_restore_after_timer_proof(self):
        run_id = "watchdog-run02"
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        router_calls = []

        def fake_router(op, current_run):
            router_calls.append(op)
            if op == "arm":
                return {
                    "status": "ARMED", "run_id": current_run, "mtu": 1200,
                    "timer_active": True, "deadline_epoch": int(time.time()) + 90, "interface": "enp0s9",
                }
            if op == "inspect":
                return {
                    "status": "NO_ACTIVE_LEASE", "run_id": current_run, "mtu": 1500,
                    "timer_active": False, "interface": "enp0s9",
                }
            raise AssertionError("manual restore must not be used in successful watchdog mode")

        def fake_guest(role, action):
            if action == "attest":
                return {"role": role}
            if action == "health":
                return {"ok": True, "status": 200, "body": "healthy"}
            return {"ok": True, "status": 200, "digest": "a" * 64}

        with (
            patch.object(drill, "ROOT", Path(temp.name)),
            patch.object(drill, "require_authorization"),
            patch.object(drill, "require_independent_vms"),
            patch.object(drill, "require_http_proof"),
            patch.object(drill, "guest_call", side_effect=fake_guest),
            patch.object(drill, "router_call", side_effect=fake_router),
            patch.object(drill, "wait_for_watchdog", return_value=95.0),
        ):
            result = drill.drill(run_id, rollback_mode="watchdog")

        self.assertEqual(result, 0)
        self.assertEqual(router_calls, ["arm", "inspect"])
        evidence = Path(temp.name) / "evidence" / run_id
        status = json.loads((evidence / "status.json").read_text())
        watchdog = json.loads((evidence / "watchdog.json").read_text())
        rollback = json.loads((evidence / "rollback.json").read_text())
        self.assertEqual(status["watchdog"], "PASS")
        self.assertEqual(watchdog["status"], "NO_ACTIVE_LEASE")
        self.assertFalse(watchdog["timer_active"])
        self.assertEqual(watchdog["mtu"], 1500)
        self.assertEqual(rollback["source"], "systemd-watchdog")

    def test_failed_watchdog_proof_falls_back_to_manual_recovery_but_stays_unverified(self):
        run_id = "watchdog-run03"
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        router_calls = []

        def fake_router(op, current_run):
            router_calls.append(op)
            if op == "arm":
                return {
                    "status": "ARMED", "run_id": current_run, "mtu": 1200,
                    "timer_active": True, "deadline_epoch": 1090, "interface": "enp0s9",
                }
            if op == "inspect":
                return {
                    "status": "ARMED", "run_id": current_run, "mtu": 1200,
                    "timer_active": False, "deadline_epoch": 1090, "interface": "enp0s9",
                }
            if op == "restore":
                return {"status": "RESTORED", "run_id": current_run, "mtu": 1500}
            raise AssertionError(op)

        def fake_guest(role, action):
            if action == "attest":
                return {"role": role}
            if action == "health":
                return {"ok": True, "status": 200, "body": "healthy"}
            return {"ok": True, "status": 200, "digest": "a" * 64}

        with (
            patch.object(drill, "ROOT", Path(temp.name)),
            patch.object(drill, "require_authorization"),
            patch.object(drill, "require_independent_vms"),
            patch.object(drill, "require_http_proof"),
            patch.object(drill, "guest_call", side_effect=fake_guest),
            patch.object(drill, "router_call", side_effect=fake_router),
            patch.object(drill, "wait_for_watchdog", return_value=95.0),
        ):
            result = drill.drill(run_id, rollback_mode="watchdog")

        self.assertEqual(result, 1)
        self.assertEqual(router_calls, ["arm", "inspect", "restore"])
        evidence = Path(temp.name) / "evidence" / run_id
        status = json.loads((evidence / "status.json").read_text())
        self.assertEqual(status["result"], "NOT_VERIFIED")
        self.assertEqual(status["watchdog"], "NOT_VERIFIED")
        self.assertTrue((evidence / "recovery.json").is_file())


if __name__ == "__main__":
    unittest.main()
