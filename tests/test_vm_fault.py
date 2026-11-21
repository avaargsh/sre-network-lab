"""Fail-closed contracts for LAB-03D router VM rollback lease (offline only)."""
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
VM = ROOT / "environments" / "vm"
sys.path.insert(0, str(VM))
spec = importlib.util.spec_from_file_location("router_fault", VM / "router_fault.py")
fault = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fault)


def guest_context():
    return {
        "lab": "sre-lab03d-vbox-intnet", "role": "router",
        "virtualization": "oracle", "default_iface": "enp0s3",
        "ip_forward": "1",
        "links": {"enp0s8": "10.209.10.1", "enp0s9": "10.209.20.1"},
        "boot_id": "11111111-2222-4333-8444-555555555555",
    }


class FaultSafetyContracts(unittest.TestCase):
    def test_requires_explicit_expiring_opt_in(self):
        fault.require_authorization("1", "sre-lab03d-vbox-intnet")
        for ack, scope in [("", "sre-lab03d-vbox-intnet"),
                           ("0", "sre-lab03d-vbox-intnet"),
                           ("1", "production")]:
            with self.assertRaises(ValueError):
                fault.require_authorization(ack, scope)

    def test_private_interface_only_not_management(self):
        self.assertEqual(fault.choose_private_link(guest_context()), "enp0s9")
        c = guest_context()
        c["default_iface"] = "enp0s9"
        with self.assertRaises(ValueError):
            fault.choose_private_link(c)
        c = guest_context()
        c["links"]["enp0s9"] = "10.209.20.254"
        with self.assertRaises(ValueError):
            fault.choose_private_link(c)
        c = guest_context()
        c["links"]["another"] = "10.209.20.1"
        with self.assertRaises(ValueError):
            fault.choose_private_link(c)

    def test_guest_identity_and_routes_are_required(self):
        c = guest_context()
        c["virtualization"] = "kvm"
        with self.assertRaises(ValueError):
            fault.choose_private_link(c)
        c = guest_context()
        c["role"] = "src"
        with self.assertRaises(ValueError):
            fault.choose_private_link(c)
        c = guest_context()
        c["ip_forward"] = "0"
        with self.assertRaises(ValueError):
            fault.choose_private_link(c)

    def test_run_id_is_not_command_injection(self):
        fault.require_run_id("vm-fault-20261121")
        for raw in ["", "../../etc", "a;b", " space", "a" * 84, "x\n--foo"]:
            with self.assertRaises(ValueError):
                fault.require_run_id(raw)

    def test_rollback_timer_must_be_scheduled_before_mtu_change(self):
        actions = fault.build_arm_steps("safe-run01", "enp0s9", 1500)
        self.assertEqual(actions[0][0], "systemd-run")
        self.assertIn("--on-active=90s", actions[0])
        self.assertIn(fault.HELPER, actions[0])
        self.assertEqual(actions[1], ("ip", "link", "set", "dev", "enp0s9", "mtu", "1200"))
        self.assertNotIn("enp0s3", str(actions))

    def test_state_recovery_must_be_same_boot(self):
        state = {"run_id": "safe-run01", "iface": "enp0s9",
                 "original_mtu": 1500, "boot_id": guest_context()["boot_id"]}
        fault.validate_state(state, guest_context())
        with self.assertRaises(ValueError):
            fault.validate_state({**state, "iface": "enp0s3"}, guest_context())
        with self.assertRaises(ValueError):
            fault.validate_state({**state, "original_mtu": 9000}, guest_context())
        with self.assertRaises(ValueError):
            fault.validate_state({**state, "boot_id": "wrong"}, guest_context())

    def test_refuses_to_arm_nonbaseline_or_active_lease(self):
        fault.require_arm_preconditions(1500, False)
        for mtu, active in [(1200, False), (1500, True), (9000, False)]:
            with self.assertRaises(ValueError):
                fault.require_arm_preconditions(mtu, active)

    def test_remote_operations_are_closed_and_bounded(self):
        for op in ("arm", "inspect", "restore"):
            args = fault.remote_argv(op, "safe-run01")
            self.assertIn("/usr/bin/python3", args)
            self.assertIn(fault.HELPER, args)
            self.assertEqual(args[-2:], (op, "safe-run01"))
        with self.assertRaises(ValueError):
            fault.remote_argv("shell", "safe-run01")

if __name__ == "__main__":
    unittest.main()
