"""Evidence replay cannot promote a manual rollback into watchdog success."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
VM = ROOT / "environments" / "vm"
sys.path.insert(0, str(VM))
sys.path.insert(0, str(ROOT / "tests"))
from test_vm_underlay import SRC, ROUTER, DST
spec = importlib.util.spec_from_file_location("verify_vm_fault", VM / "verify_vm_fault.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class EvidenceContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "safe-run01"
        self.path.mkdir()
        self.r = {
            "topology": copy.deepcopy({"src": SRC, "router": ROUTER, "dst": DST}),
            "baseline": {
                "health": {"ok": True, "status": 200, "body": "healthy"},
                "upload": {"ok": True, "status": 200, "digest": "a" * 64},
            },
            "fault_health": {"ok": True, "status": 200, "body": "healthy"},
            "fault_upload": {"ok": True, "status": 200, "digest": "a" * 64},
            "lease": {
                "status": "ARMED", "run_id": "safe-run01", "mtu": 1200,
                "timer_active": True, "deadline_epoch": 1090, "interface": "enp0s9",
            },
            "fault_inspect": {
                "status": "ARMED", "run_id": "safe-run01", "mtu": 1200,
                "timer_active": True, "deadline_epoch": 1090, "interface": "enp0s9",
            },
            "watchdog": {"status": "NOT_RUN", "run_id": "safe-run01"},
            "rollback": {"status": "RESTORED", "run_id": "safe-run01", "mtu": 1500},
            "recovery": {
                "health": {"ok": True, "status": 200, "body": "healthy"},
                "upload": {"ok": True, "status": 200, "digest": "a" * 64},
            },
            "status": {
                "result": "PASS", "topology": "PASS", "baseline_http": "PASS",
                "mtu_lease": "PASS", "recovery_http": "PASS",
                "watchdog": "NOT_VERIFIED",
                "network": "NOT_VERIFIED", "pcap": "NOT_VERIFIED",
                "blackhole": "NOT_VERIFIED",
                "scope": "3 VirtualBox VMs; actual E2E required",
            },
        }

    def tearDown(self):
        self.temp.cleanup()

    def save(self):
        for key, value in self.r.items():
            (self.path / (key + ".json")).write_text(json.dumps(value))
        hashes = [
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}"
            for path in sorted(self.path.iterdir()) if path.name != "SHA256SUMS"
        ]
        (self.path / "SHA256SUMS").write_text("\n".join(hashes) + "\n")

    def set_valid_watchdog_receipts(self):
        self.r["watchdog"] = {
            "status": "NO_ACTIVE_LEASE", "run_id": "safe-run01",
            "interface": "enp0s9", "mtu": 1500, "timer_active": False,
            "wait_started_epoch": 1000, "wait_finished_epoch": 1095,
            "waited_seconds": 95.0,
        }
        self.r["rollback"] = {
            "status": "RESTORED", "run_id": "safe-run01", "mtu": 1500,
            "source": "systemd-watchdog",
        }
        self.r["status"]["watchdog"] = "PASS"

    def test_valid_manual_mtu_receipts_pass_but_watchdog_stays_unverified(self):
        self.save()
        result = audit.verify(self.path, "a" * 64)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["watchdog"], "NOT_VERIFIED")

    def test_valid_timer_driven_watchdog_receipts_pass(self):
        self.set_valid_watchdog_receipts()
        self.save()
        result = audit.verify(self.path, "a" * 64)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["watchdog"], "PASS")

    def test_cannot_claim_watchdog_success_while_timer_is_active(self):
        self.set_valid_watchdog_receipts()
        self.r["watchdog"]["timer_active"] = True
        self.save()
        with self.assertRaises(ValueError):
            audit.verify(self.path, "a" * 64)

    def test_cannot_claim_watchdog_success_without_full_elapsed_window(self):
        self.set_valid_watchdog_receipts()
        self.r["watchdog"]["waited_seconds"] = 10
        self.r["watchdog"]["wait_finished_epoch"] = 1010
        self.save()
        with self.assertRaises(ValueError):
            audit.verify(self.path, "a" * 64)

    def test_cannot_claim_watchdog_success_without_systemd_rollback_receipt(self):
        self.set_valid_watchdog_receipts()
        self.r["rollback"]["source"] = "manual-ssh"
        self.save()
        with self.assertRaises(ValueError):
            audit.verify(self.path, "a" * 64)

    def test_cannot_claim_unverified_pcap(self):
        self.r["status"]["pcap"] = "PASS"
        self.save()
        with self.assertRaises(ValueError):
            audit.verify(self.path, "a" * 64)

    def test_recovery_is_required_even_with_valid_arm(self):
        self.r["rollback"]["status"] = "NO_ACTIVE_LEASE"
        self.save()
        with self.assertRaises(ValueError):
            audit.verify(self.path, "a" * 64)

    def test_tampered_hash_fails(self):
        self.save()
        (self.path / "rollback.json").write_text("{}")
        with self.assertRaises(ValueError):
            audit.verify(self.path, "a" * 64)

    def test_duplicate_vm_identity_fails(self):
        self.r["topology"]["dst"]["machine_id"] = self.r["topology"]["src"]["machine_id"]
        self.save()
        with self.assertRaises(ValueError):
            audit.verify(self.path, "a" * 64)

    def test_uncovered_extra_file_fails(self):
        self.save()
        (self.path / "extra.txt").write_text("not accounted for")
        with self.assertRaises(ValueError):
            audit.verify(self.path, "a" * 64)


if __name__ == "__main__":
    unittest.main()
