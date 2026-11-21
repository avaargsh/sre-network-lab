"""Opt-in *real* VirtualBox 3-guest E2E entrypoint static safety contracts.

Only proves configuration safety; does not claim a VM was launched by CI.
"""
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"environments"/"vm"/"run_live_e2e.sh"
WORKFLOW=ROOT/".github"/"workflows"/"lab03d-real-vbox.yml"


class RealVMSafety(unittest.TestCase):
    def test_real_vm_workflow_is_only_manual_and_self_hosted(self):
        content=WORKFLOW.read_text()
        self.assertIn("workflow_dispatch:",content)
        self.assertNotIn("pull_request:",content)
        self.assertNotIn("push:",content)
        self.assertIn("self-hosted",content)
        self.assertIn("sre-vbox-lab",content)
        self.assertIn("refs/heads/main",content)
        self.assertIn("I_CONFIRM_DEDICATED_VBOX_HOST",content)

    def test_manual_workflow_never_claims_mtu_fault(self):
        content=WORKFLOW.read_text()
        self.assertIn("run_live_e2e.sh",content)
        self.assertIn("upload-artifact",content)
        self.assertNotIn("vm_mtu_drill.py",content)
        self.assertNotIn("SRE_LAB_VM_FAULT_ACK",content)

    def test_shell_preflight_requires_dedicated_host_and_nonroot(self):
        content=SCRIPT.read_text()
        for required in (
            "SRE_LAB_LIVE_E2E_ACK",
            "I_CONFIRM_DEDICATED_VBOX_HOST",
            "SRE_LAB_VM_SCOPE",
            "sre-lab03d-vbox-intnet",
            "VBoxManage", "vagrant", "id -u",
            "not_created",
        ):
            self.assertIn(required,content)

    def test_owned_vm_destroy_is_conditional_and_trapped(self):
        content=SCRIPT.read_text()
        self.assertIn("trap cleanup EXIT",content)
        self.assertIn("created_by_this_run",content)
        self.assertIn("vagrant destroy -f",content)
        self.assertIn("vagrant up --provider=virtualbox",content)

    def test_real_evidence_includes_baseline_and_independent_pcap(self):
        content=SCRIPT.read_text()
        for required in (
            "lab_vm.py baseline", "verify_vm_evidence.py",
            "vm_pcap_baseline.py baseline-pcap", "verify_vm_pcap.py",
        ):
            self.assertIn(required,content)

if __name__=="__main__":
    unittest.main()
