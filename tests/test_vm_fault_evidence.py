"""Evidence replay cannot promote a synthetic MTU lease to multi-VM PCAP success."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
VM=ROOT/"environments"/"vm"
sys.path.insert(0,str(VM))
sys.path.insert(0,str(ROOT/"tests"))
from test_vm_underlay import SRC, ROUTER, DST
spec=importlib.util.spec_from_file_location("verify_vm_fault",VM/"verify_vm_fault.py")
audit=importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)

class EvidenceContracts(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/"safe-run01"
        self.path.mkdir()
        self.r={
          "topology":copy.deepcopy({"src":SRC,"router":ROUTER,"dst":DST}),
          "baseline":{"health":{"ok":True,"status":200,"body":"healthy"},
                      "upload":{"ok":True,"status":200,"digest":"a"*64}},
          "fault_health":{"ok":True,"status":200,"body":"healthy"},
          "fault_upload":{"ok":True,"status":200,"digest":"a"*64},
          "lease":{"status":"ARMED","run_id":"safe-run01","mtu":1200,
                   "timer_active":True,"deadline_epoch":1090,"interface":"enp0s9"},
          "fault_inspect":{"status":"ARMED","run_id":"safe-run01","mtu":1200,
                   "timer_active":True,"deadline_epoch":1090,"interface":"enp0s9"},
          "rollback":{"status":"RESTORED","run_id":"safe-run01","mtu":1500},
          "recovery":{"health":{"ok":True,"status":200,"body":"healthy"},
                      "upload":{"ok":True,"status":200,"digest":"a"*64}},
          "status":{"result":"PASS", "topology":"PASS", "baseline_http":"PASS",
                    "mtu_lease":"PASS","recovery_http":"PASS",
                    "network":"NOT_VERIFIED","pcap":"NOT_VERIFIED",
                    "blackhole":"NOT_VERIFIED",
                    "scope":"3 VirtualBox VMs; actual E2E required"}
        }

    def tearDown(self):
        self.temp.cleanup()

    def save(self):
        for k,v in self.r.items():
            (self.path/(k+".json")).write_text(json.dumps(v))
        hashes = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}"
                  for p in sorted(self.path.iterdir()) if p.name!="SHA256SUMS"]
        (self.path/"SHA256SUMS").write_text("\n".join(hashes)+"\n")

    def test_valid_mtu_only_receipts_pass(self):
        self.save()
        self.assertEqual(audit.verify(self.path, "a"*64)["status"],"PASS")

    def test_cannot_claim_unverified_pcap(self):
        self.r["status"]["pcap"]="PASS"
        self.save()
        with self.assertRaises(ValueError):
            audit.verify(self.path,"a"*64)

    def test_recovery_is_required_even_with_valid_arm(self):
        self.r["rollback"]["status"]="NO_ACTIVE_LEASE"
        self.save()
        with self.assertRaises(ValueError):
            audit.verify(self.path,"a"*64)

    def test_tampered_hash_fails(self):
        self.save()
        (self.path/"rollback.json").write_text("{}")
        with self.assertRaises(ValueError):
            audit.verify(self.path,"a"*64)

    def test_duplicate_vm_identity_fails(self):
        self.r["topology"]["dst"]["machine_id"]=self.r["topology"]["src"]["machine_id"]
        self.save()
        with self.assertRaises(ValueError):
            audit.verify(self.path,"a"*64)

    def test_uncovered_extra_file_fails(self):
        self.save()
        (self.path/"extra.txt").write_text("not accounted for")
        with self.assertRaises(ValueError):
            audit.verify(self.path,"a"*64)

if __name__=="__main__":
    unittest.main()
