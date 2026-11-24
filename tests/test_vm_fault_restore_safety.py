"""LAB-03D restore must not overwrite MTU changes outside its lease."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

VM = Path(__file__).resolve().parents[1] / "environments" / "vm"
sys.path.insert(0, str(VM))
spec = importlib.util.spec_from_file_location("router_fault_restore_safety", VM / "router_fault.py")
fault = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fault)


def lease():
    return {
        "run_id": "safe-run01",
        "iface": "enp0s9",
        "original_mtu": 1500,
        "boot_id": "11111111-2222-4333-8444-555555555555",
        "deadline_epoch": 1090,
    }


class RestoreSafetyContracts(unittest.TestCase):
    def restore_with_mtu(self, mtu_values):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        state_file = Path(temp.name) / "mtu-state.json"
        state_file.write_text("{}")
        calls = []

        def fake_cmd(*args, **kwargs):
            calls.append(args)
            return type("Result", (), {"returncode": 0})()

        context = {
            "lab": fault.LAB,
            "role": "router",
            "virtualization": "oracle",
            "default_iface": "enp0s3",
            "ip_forward": "1",
            "links": {"enp0s8": "10.209.10.1", "enp0s9": "10.209.20.1"},
            "boot_id": lease()["boot_id"],
        }
        patches = (
            patch.object(fault, "STATE", state_file),
            patch.object(fault, "choose_private_link", return_value="enp0s9"),
            patch.object(fault, "load_state", return_value=lease()),
            patch.object(fault, "validate_state"),
            patch.object(fault, "current_mtu", side_effect=mtu_values),
            patch.object(fault, "cmd", side_effect=fake_cmd),
        )
        stack = []
        try:
            for item in patches:
                stack.append(item)
                item.__enter__()
            result = fault.restore("safe-run01", context)
        except Exception:
            raise
        finally:
            for item in reversed(stack):
                item.__exit__(None, None, None)
        return result, calls, state_file

    def test_unexpected_mtu_is_preserved_and_lease_remains(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        state_file = Path(temp.name) / "mtu-state.json"
        state_file.write_text("{}")
        calls = []
        context = {"lab": fault.LAB, "role": "router", "virtualization": "oracle",
                   "default_iface": "enp0s3", "ip_forward": "1",
                   "links": {"enp0s8": "10.209.10.1", "enp0s9": "10.209.20.1"},
                   "boot_id": lease()["boot_id"]}
        with (
            patch.object(fault, "STATE", state_file),
            patch.object(fault, "choose_private_link", return_value="enp0s9"),
            patch.object(fault, "load_state", return_value=lease()),
            patch.object(fault, "validate_state"),
            patch.object(fault, "current_mtu", return_value=1400),
            patch.object(fault, "cmd", side_effect=lambda *a, **k: calls.append(a)),
        ):
            with self.assertRaisesRegex(ValueError, "unexpected MTU"):
                fault.restore("safe-run01", context)
        self.assertEqual(calls, [])
        self.assertTrue(state_file.exists())

    def test_already_restored_mtu_is_idempotent_without_rewriting_link(self):
        result, calls, state_file = self.restore_with_mtu([1500])
        self.assertEqual(result, {"status": "RESTORED", "run_id": "safe-run01", "mtu": 1500})
        self.assertEqual(calls, [("systemctl", "stop", "sre-lab03d-mtu-safe-run01.timer")])
        self.assertFalse(state_file.exists())

    def test_owned_fault_mtu_is_restored_and_read_back(self):
        result, calls, state_file = self.restore_with_mtu([1200, 1500])
        self.assertEqual(result["status"], "RESTORED")
        self.assertEqual(calls[0], ("ip", "link", "set", "dev", "enp0s9", "mtu", "1500"))
        self.assertEqual(calls[1], ("systemctl", "stop", "sre-lab03d-mtu-safe-run01.timer"))
        self.assertFalse(state_file.exists())


if __name__ == "__main__":
    unittest.main()
