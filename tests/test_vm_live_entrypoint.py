"""Opt-in *real* VirtualBox 3-guest E2E entrypoint static safety contracts.

Only proves configuration safety; does not claim a VM was launched by CI.
"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "environments" / "vm" / "run_live_e2e.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "lab03d-real-vbox.yml"


class RealVMSafety(unittest.TestCase):
    def test_real_vm_workflow_is_only_manual_and_self_hosted(self):
        content = WORKFLOW.read_text()
        self.assertIn("workflow_dispatch:", content)
        self.assertNotIn("pull_request:", content)
        self.assertNotIn("push:", content)
        self.assertIn("self-hosted", content)
        self.assertIn("sre-vbox-lab", content)
        self.assertIn("refs/heads/main", content)
        self.assertIn("I_CONFIRM_DEDICATED_VBOX_HOST", content)

    def test_watchdog_injection_requires_a_second_explicit_confirmation(self):
        workflow = WORKFLOW.read_text()
        script = SCRIPT.read_text()
        self.assertIn("watchdog_confirmation:", workflow)
        self.assertIn("I_CONFIRM_ROUTER_MTU_WATCHDOG", workflow)
        self.assertIn("SRE_LAB_VM_WATCHDOG_ACK", workflow)
        self.assertIn('watchdog_ack="${SRE_LAB_VM_WATCHDOG_ACK:-SKIP}"', script)
        self.assertIn('[[ "$watchdog_ack" == "I_CONFIRM_ROUTER_MTU_WATCHDOG" ]]', script)
        self.assertIn("SRE_LAB_VM_FAULT_ACK=1", script)
        self.assertIn("vm_mtu_drill.py drill --rollback-mode watchdog", script)
        self.assertIn("verify_vm_fault.py", script)

    def test_shell_preflight_requires_dedicated_host_and_nonroot(self):
        content = SCRIPT.read_text()
        for required in (
            "SRE_LAB_LIVE_E2E_ACK",
            "I_CONFIRM_DEDICATED_VBOX_HOST",
            "SRE_LAB_VM_SCOPE",
            "sre-lab03d-vbox-intnet",
            "VBoxManage", "vagrant", "id -u",
            "not_created",
        ):
            self.assertIn(required, content)

    def test_owned_vm_destroy_is_conditional_and_trapped(self):
        content = SCRIPT.read_text()
        self.assertIn("trap cleanup EXIT", content)
        self.assertIn("created_by_this_run", content)
        self.assertIn("vagrant destroy -f", content)
        self.assertIn("vagrant up --provider=virtualbox", content)

    def test_real_evidence_includes_baseline_and_independent_pcap(self):
        content = SCRIPT.read_text()
        for required in (
            "lab_vm.py baseline", "verify_vm_evidence.py",
            "vm_pcap_baseline.py baseline-pcap", "verify_vm_pcap.py",
        ):
            self.assertIn(required, content)

    def test_watchdog_is_not_run_when_confirmation_is_absent(self):
        content = SCRIPT.read_text()
        self.assertIn('else\n  echo "REAL_VM_WATCHDOG_NOT_RUN: second confirmation was not supplied"', content)


if __name__ == "__main__":
    unittest.main()
