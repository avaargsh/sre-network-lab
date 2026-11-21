"""Host four-VM-NIC capture orchestration contracts; no VM needed."""
import base64
import copy
import hashlib
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
VM=ROOT/"environments"/"vm"
sys.path.insert(0,str(VM))
sys.path.insert(0,str(ROOT/"tests"))
from test_vm_underlay import SRC, ROUTER, DST
from test_vm_pcap import frame, packet_file, post_frames
spec=importlib.util.spec_from_file_location("vm_pcap_baseline",VM/"vm_pcap_baseline.py")
host=importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)

def topology():
    return copy.deepcopy({"src":SRC,"router":ROUTER,"dst":DST})

def finished(role,run_id):
    sides={"src":{"src":"enp0s8"},"router":{"left":"enp0s8","right":"enp0s9"},
           "dst":{"dst":"enp0s8"}}[role]
    output={}
    for side,iface in sides.items():
        data=packet_file(*post_frames())
        output[side]={"interface":iface,"size":len(data),
                      "sha256":hashlib.sha256(data).hexdigest(),
                      "data":base64.b64encode(data).decode("ascii")}
    return {"action":"finish","role":role,"run_id":run_id,
            "status":"STOPPED","captures":output}

class HostCaptureContracts(unittest.TestCase):
    def test_rejects_forged_vagrant_stdout_and_foreign_action(self):
        valid='SRE_LAB_PCAP={"action":"start","role":"src","run_id":"run01"}\n'
        self.assertEqual(host.parse_guest_output(valid)["role"],"src")
        for value in ("no report",valid+valid,"SRE_LAB_PCAP=[]"):
            with self.assertRaises(ValueError):
                host.parse_guest_output(value)
        with self.assertRaises(ValueError):
            host.guest_capture("production","start","run01")

    def test_refuses_mismatched_capture_owner_even_with_valid_digest(self):
        t=topology()
        receipt=finished("router","run01")
        receipt["captures"]["right"]["interface"]="enp0s3"
        with self.assertRaises(ValueError):
            host.decode_receipt(receipt,"router",t)

    def test_start_requires_bounded_runtime_and_private_nics(self):
        t=topology()
        good={"status":"ARMED","runtime_max_seconds":120,"packet_cap":512,
              "interfaces":{"left":"enp0s8","right":"enp0s9"}}
        host.require_started(good,"router",t)
        with self.assertRaises(ValueError):
            host.require_started({**good,"packet_cap":0},"router",t)
        with self.assertRaises(ValueError):
            host.require_started({**good,"interfaces":{"left":"enp0s3","right":"enp0s9"}},
                                 "router",t)

    def test_business_failure_stops_all_started_capture_services(self):
        events=[]
        with tempfile.TemporaryDirectory() as tmp:
            def fakeguest(role,op):
                if op=="attest":
                    return topology()[role]
                if op=="health":
                    return {"ok":True,"status":200,"body":"healthy"}
                if op=="upload":
                    # First baseline succeeds; on-wire upload fails.
                    calls["upload"]+=1
                    if calls["upload"] == 2:
                        raise RuntimeError("network unavailable")
                    from http_vm import SHA256
                    return {"ok":True,"status":200,"digest":SHA256}
            calls={"upload":0}
            def fakecap(role,op,run_id):
                events.append((role,op))
                if op=="start":
                    return {"status":"ARMED","runtime_max_seconds":120,"packet_cap":512,
                            "interfaces":{"src":{"src":"enp0s8"},
                                          "router":{"left":"enp0s8","right":"enp0s9"},
                                          "dst":{"dst":"enp0s8"}}[role]}
                return finished(role,run_id)
            with (mock.patch.object(host,"ROOT",Path(tmp)),
                  mock.patch.object(host,"guest_call",side_effect=fakeguest),
                  mock.patch.object(host,"guest_capture",side_effect=fakecap)):
                value=host.capture_baseline("test-run01")
            self.assertEqual(value,1)
            self.assertEqual(events[-3:],[
                ("dst","finish"),("router","finish"),("src","finish")])
            self.assertEqual(calls["upload"],2)
            self.assertTrue((Path(tmp)/"evidence"/"test-run01"/"failure.json").is_file())

    def test_independent_audit_failure_must_demote_status_from_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            topo=topology()
            def fakeguest(role,op):
                if op=="attest":return topo[role]
                if op=="health":return {"ok":True,"status":200,"body":"healthy"}
                from http_vm import SHA256
                return {"ok":True,"status":200,"digest":SHA256}
            def fakecap(role,action,run_id):
                if action=="finish":return finished(role,run_id)
                return {"status":"ARMED","runtime_max_seconds":120,"packet_cap":512,
                        "interfaces":{"src":{"src":"enp0s8"},
                                      "router":{"left":"enp0s8","right":"enp0s9"},
                                      "dst":{"dst":"enp0s8"}}[role]}
            with (mock.patch.object(host,"ROOT",Path(tmp)),
                  mock.patch.object(host,"guest_call",side_effect=fakeguest),
                  mock.patch.object(host,"guest_capture",side_effect=fakecap),
                  mock.patch.object(host,"verify",side_effect=ValueError("independent gate failed"))):
                self.assertEqual(host.capture_baseline("test-run02"),1)
            import json
            folder=Path(tmp)/"evidence"/"test-run02"
            self.assertEqual(json.loads((folder/"status.json").read_text())["result"],"NOT_VERIFIED")

if __name__=="__main__":
    unittest.main()
