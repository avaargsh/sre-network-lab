"""LAB-03D independent four-point evidence replay (all synthetic test fixtures)."""
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
from test_vm_pcap import packet_file, frame, post_frames
spec=importlib.util.spec_from_file_location("verify_vm_pcap",VM/"verify_vm_pcap.py")
auditor=importlib.util.module_from_spec(spec)
spec.loader.exec_module(auditor)

class AuditedCapture(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.topology=copy.deepcopy({"src":SRC,"router":ROUTER,"dst":DST})
        self.http={"health":{"ok":True,"status":200,"body":"healthy"},
                   "upload":{"ok":True,"status":200,"digest":"a"*64}}
        self.status={"result":"PASS",
                     "scope":"3 independent VirtualBox VM NICs / 4 TCP captures",
                     "pcap":"PASS","http":"PASS",
                     "mtu_fault":"NOT_VERIFIED","tcp_blackhole":"NOT_VERIFIED"}
        self.meta={}
        self.pcap={}
        for side,(role,ip,iface) in {
            "src":("src","10.209.10.2","enp0s8"),
            "router-left":("router","10.209.10.1","enp0s8"),
            "router-right":("router","10.209.20.1","enp0s9"),
            "dst":("dst","10.209.20.2","enp0s8"),
        }.items():
            blob=packet_file(*post_frames())
            self.pcap[side]=blob
            self.meta[side]={"role":role,"private_ip":ip,"interface":iface,
                             "size":len(blob),"sha256":hashlib.sha256(blob).hexdigest()}

    def tearDown(self):
        self.temp.cleanup()

    def write(self):
        for name,obj in [("topology",self.topology),("http",self.http),
                         ("status",self.status),("capture-metadata",self.meta)]:
            (self.root/(name+".json")).write_text(json.dumps(obj))
        for side,raw in self.pcap.items():
            (self.root/(side+".pcap")).write_bytes(raw)
        rows=[f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}"
              for p in sorted(self.root.iterdir()) if p.name!="SHA256SUMS"]
        (self.root/"SHA256SUMS").write_text("\n".join(rows)+"\n")

    def test_complete_evidence_is_independently_accepted(self):
        self.write()
        result=auditor.verify(self.root,"a"*64)
        self.assertEqual(result["status"],"PASS")
        self.assertEqual(result["files_hashed"],8)
        self.assertEqual(result["flow"]["source_port"],45000)

    def test_corrupted_binary_fails(self):
        self.write()
        (self.root/"router-right.pcap").write_bytes(b"BAD")
        with self.assertRaises(ValueError):
            auditor.verify(self.root,"a"*64)

    def test_foreign_router_interface_fails_even_with_new_hashes(self):
        self.meta["router-right"]["interface"]="enp0s3"
        self.write()
        with self.assertRaises(ValueError):
            auditor.verify(self.root,"a"*64)

    def test_missing_distinct_vm_fails_even_with_new_hashes(self):
        self.topology["dst"]["machine_id"]=self.topology["src"]["machine_id"]
        self.write()
        with self.assertRaises(ValueError):
            auditor.verify(self.root,"a"*64)

    def test_falsely_claimed_mtu_blackhole_fails(self):
        self.status["tcp_blackhole"]="PASS"
        self.write()
        with self.assertRaises(ValueError):
            auditor.verify(self.root,"a"*64)

    def test_real_post_digest_cannot_be_forged(self):
        self.http["upload"]["digest"]="f"*64
        self.write()
        with self.assertRaises(ValueError):
            auditor.verify(self.root,"a"*64)

    def test_extra_unaccounted_file_fails(self):
        self.write()
        (self.root/"extra.log").write_text("surprise")
        with self.assertRaises(ValueError):
            auditor.verify(self.root,"a"*64)

if __name__=="__main__":
    unittest.main()
