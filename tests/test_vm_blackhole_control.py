"""LAB-03D Router VM TCP PMTU blackhole controls: bounded and fail-closed."""
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest import mock

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


def active_state():
    names = fault.firewall_chain_names("safe-run01")
    return {
        "run_id": "safe-run01", "iface": "enp0s9", "original_mtu": 1500,
        "boot_id": guest_context()["boot_id"], "deadline_epoch": 1090,
        "mode": "tcp-blackhole", "left_iface": "enp0s8",
        "icmp_chain": names["icmp"], "mss_chain": None,
        "firewall_tag": fault.firewall_tag("safe-run01"),
    }


class BlackholeScopeContracts(unittest.TestCase):
    def test_private_links_are_resolved_by_address_not_interface_order(self):
        self.assertEqual(fault.choose_private_links(guest_context()),
                         {"left": "enp0s8", "right": "enp0s9"})
        c = guest_context()
        c["default_iface"] = "enp0s8"
        with self.assertRaises(ValueError):
            fault.choose_private_links(c)

    def test_firewall_rules_are_limited_to_lab_icmp_and_tcp_flows(self):
        spec = fault.blackhole_rule_specs("safe-run01", guest_context())
        drop = spec["icmp"]["rules"][0]
        self.assertEqual(spec["icmp"]["table"], "filter")
        self.assertEqual(spec["icmp"]["parent"], "OUTPUT")
        self.assertIn("enp0s8", drop)
        self.assertIn("fragmentation-needed", drop)
        self.assertIn("10.209.10.2", drop)
        self.assertIn("DROP", drop)
        self.assertNotIn("enp0s3", drop)

        rules = spec["mss"]["rules"]
        self.assertEqual(spec["mss"]["table"], "mangle")
        self.assertEqual(spec["mss"]["parent"], "FORWARD")
        self.assertEqual(len(rules), 2)
        self.assertTrue(any("10.209.10.2" in rule and "10.209.20.2" in rule
                            and "enp0s8" in rule and "enp0s9" in rule
                            for rule in rules))
        self.assertTrue(any("10.209.20.2" in rule and "10.209.10.2" in rule
                            and "enp0s9" in rule and "enp0s8" in rule
                            for rule in rules))
        self.assertTrue(all("1160" in rule and "TCPMSS" in rule for rule in rules))

    def test_chain_names_are_deterministic_and_run_scoped(self):
        first = fault.firewall_chain_names("safe-run01")
        self.assertEqual(first, fault.firewall_chain_names("safe-run01"))
        self.assertNotEqual(first, fault.firewall_chain_names("safe-run02"))
        self.assertTrue(all(name.isalnum() and len(name) <= 28 for name in first.values()))
        with self.assertRaises(ValueError):
            fault.firewall_chain_names("../unsafe")


class BlackholeLeaseContracts(unittest.TestCase):
    def test_orphaned_firewall_state_blocks_a_new_run(self):
        result = mock.Mock(returncode=0, stdout="-N S3I12345678\\n")
        with mock.patch.object(fault, "ipt", return_value=result):
            with self.assertRaises(ValueError):
                fault.ensure_no_orphaned_lab_rules()

    def test_blackhole_watchdog_is_armed_before_icmp_drop_and_mtu_change(self):
        events = []
        with (
            mock.patch.object(fault, "current_mtu", side_effect=[1500, 1200]),
            mock.patch.object(fault, "load_state", return_value=None),
            mock.patch.object(fault, "timer_active", side_effect=[False, True]),
            mock.patch.object(fault, "store_state"),
            mock.patch.object(fault, "ensure_no_orphaned_lab_rules"),
            mock.patch.object(fault, "chain_exists", return_value=False),
            mock.patch.object(fault, "rule_exists", return_value=False),
            mock.patch.object(fault, "cmd",
                               side_effect=lambda *a, **k: events.append(("cmd", a))),
            mock.patch.object(fault, "add_owned_chain",
                               side_effect=lambda *a: events.append(("chain", a))),
            mock.patch.object(fault, "firewall_report", return_value={
                "icmp_drop_active": True, "icmp_drop_packets": 0,
                "mss_active": False, "mss_packets": 0,
            }),
        ):
            receipt = fault.arm_blackhole("safe-run01", guest_context())

        self.assertEqual(receipt["status"], "BLACKHOLE_ARMED")
        self.assertTrue(receipt["timer_active"])
        self.assertEqual(events[0][0], "cmd")
        self.assertEqual(events[0][1][0], "systemd-run")
        self.assertEqual(events[1][0], "chain")
        self.assertEqual(events[1][1][0:2], ("filter", "OUTPUT"))
        self.assertEqual(events[2][0], "cmd")
        self.assertEqual(events[2][1][:4], ("ip", "link", "set", "dev"))

    def test_blackhole_refuses_to_mutate_when_watchdog_is_not_active(self):
        events = []
        with (
            mock.patch.object(fault, "current_mtu", return_value=1500),
            mock.patch.object(fault, "load_state", return_value=None),
            mock.patch.object(fault, "timer_active", side_effect=[False, False]),
            mock.patch.object(fault, "store_state"),
            mock.patch.object(fault, "ensure_no_orphaned_lab_rules"),
            mock.patch.object(fault, "chain_exists", return_value=False),
            mock.patch.object(fault, "rule_exists", return_value=False),
            mock.patch.object(fault, "cmd",
                               side_effect=lambda *a, **k: events.append(a)),
            mock.patch.object(fault, "add_owned_chain") as add_chain,
            mock.patch.object(fault, "restore") as rollback,
        ):
            with self.assertRaises(RuntimeError):
                fault.arm_blackhole("safe-run01", guest_context())
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][0], "systemd-run")
        add_chain.assert_not_called()
        rollback.assert_called_once_with("safe-run01", guest_context())

    def test_mss_mitigation_requires_the_owned_live_blackhole_lease(self):
        state = active_state()
        with (
            mock.patch.object(fault, "load_state", return_value=state),
            mock.patch.object(fault, "validate_state"),
            mock.patch.object(fault, "current_mtu", return_value=1200),
            mock.patch.object(fault, "timer_active", return_value=True),
            mock.patch.object(fault, "store_state"),
            mock.patch.object(fault, "add_owned_chain") as add_chain,
            mock.patch.object(fault, "firewall_report", return_value={
                "icmp_drop_active": True, "icmp_drop_packets": 3,
                "mss_active": True, "mss_packets": 2,
            }),
        ):
            receipt = fault.mitigate("safe-run01", guest_context())
        self.assertEqual(receipt["status"], "MITIGATED")
        self.assertTrue(receipt["mss_active"])
        add_chain.assert_called_once()
        self.assertEqual(add_chain.call_args.args[0:2], ("mangle", "FORWARD"))

    def test_mss_mitigation_rejects_expired_or_foreign_lease(self):
        for state, timer in ((None, True), (active_state(), False)):
            with (
                mock.patch.object(fault, "load_state", return_value=state),
                mock.patch.object(fault, "validate_state"),
                mock.patch.object(fault, "current_mtu", return_value=1200),
                mock.patch.object(fault, "timer_active", return_value=timer),
                mock.patch.object(fault, "add_owned_chain") as add_chain,
            ):
                with self.assertRaises(ValueError):
                    fault.mitigate("safe-run01", guest_context())
                add_chain.assert_not_called()

    def test_remote_operations_are_closed_and_run_id_bound(self):
        for op in ("arm-blackhole", "mitigate", "inspect", "restore"):
            argv = fault.remote_argv(op, "safe-run01")
            self.assertIn(fault.HELPER, argv)
            self.assertEqual(argv[-2:], (op, "safe-run01"))
        for op in ("shell", "iptables", "arm-blackhole;id"):
            with self.assertRaises(ValueError):
                fault.remote_argv(op, "safe-run01")


if __name__ == "__main__":
    unittest.main()
