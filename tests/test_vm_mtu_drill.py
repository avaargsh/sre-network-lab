"""LAB-03D remote fault controller: no forged success or unchecked lease."""
import importlib.util
from pathlib import Path
import sys
import unittest

P = Path(__file__).resolve().parents[1] / "environments" / "vm"
sys.path.insert(0, str(P))
spec = importlib.util.spec_from_file_location("vm_mtu_drill", P / "vm_mtu_drill.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class RemoteSafetyTests(unittest.TestCase):
    def test_requires_single_scoped_guest_report(self):
        line='noise\nSRE_LAB_FAULT={"run_id":"lab-01","status":"ARMED","mtu":1200}\n'
        self.assertEqual(m.parse_report(line)["status"], "ARMED")
        with self.assertRaises(ValueError):
            m.parse_report(line + line)
        with self.assertRaises(ValueError):
            m.parse_report("untrusted")

    def test_armed_must_have_active_timer_and_valid_deadline(self):
        r={"run_id":"lab-01","status":"ARMED", "interface":"enp0s9",
           "mtu":1200, "timer_active":True,"deadline_epoch":1090}
        m.require_armed(r, "lab-01", 1000)
        for change in [{"timer_active":False}, {"status":"READY"},
                       {"mtu":1500}, {"run_id":"another"},
                       {"deadline_epoch":1001}, {"deadline_epoch":1200}]:
            with self.assertRaises(ValueError):
                m.require_armed({**r,**change}, "lab-01", 1000)

    def test_recovery_requires_same_run_and_mtu(self):
        m.require_restored({"status":"RESTORED","run_id":"lab-01","mtu":1500}, "lab-01")
        for x in [
            {"status":"NO_ACTIVE_LEASE","run_id":"lab-01","mtu":1500},
            {"status":"RESTORED","run_id":"other","mtu":1500},
            {"status":"RESTORED","run_id":"lab-01","mtu":1200},
        ]:
            with self.assertRaises(ValueError):
                m.require_restored(x, "lab-01")

    def test_remote_invocation_cannot_run_untrusted_commands(self):
        for verb in ("arm", "inspect", "restore"):
            command=m.remote_shell(verb,"lab-01")
            self.assertIn("sudo -n",command)
            self.assertIn("/usr/local/libexec/sre-lab03d-router-fault.py",command)
        with self.assertRaises(ValueError):
            m.remote_shell("bash","lab-01")
        with self.assertRaises(ValueError):
            m.remote_shell("arm","a;id")

    def test_unverified_fault_is_not_relabelled_success(self):
        for check in ("network","pcap","blackhole"):
            self.assertEqual(m.UNVERIFIED[check], "NOT_VERIFIED")


if __name__=="__main__":
    unittest.main()
