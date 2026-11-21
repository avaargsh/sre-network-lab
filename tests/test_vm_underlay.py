"""LAB-03D: independent VM identity, routing and request evidence contracts."""
import importlib.util
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "environments" / "vm"))
spec = importlib.util.spec_from_file_location("vm_lab", ROOT / "environments/vm/lab_vm.py")
vm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vm)


def report(role, machine, boot, iface, addr, default="enp0s3", route=None):
    return {"lab":"sre-lab03d-vbox-intnet", "role":role, "virtualization":"oracle", "machine_id":machine,
            "boot_id":boot, "default_iface":default,
            "links": {iface: addr},
            "route": route or {}}


SRC = report("src", "a"*32, "11111111-1111-4111-8111-111111111111",
             "enp0s8", "10.209.10.2",
             route={"gateway":"10.209.10.1","dev":"enp0s8","src":"10.209.10.2"})
ROUTER = report("router", "b"*32, "22222222-2222-4222-8222-222222222222",
                "enp0s8", "10.209.10.1")
ROUTER["links"]["enp0s9"] = "10.209.20.1"
ROUTER["ip_forward"] = "1"
DST = report("dst", "c"*32, "33333333-3333-4333-8333-333333333333",
             "enp0s8", "10.209.20.2",
             route={"gateway":"10.209.20.1","dev":"enp0s8","src":"10.209.20.2"})


SRC["route"]["destination"] = "10.209.20.2"
DST["route"]["destination"] = "10.209.10.2"


class VMContracts(unittest.TestCase):
    def setUp(self):
        import copy
        self.reports = copy.deepcopy({"src":SRC,"router":ROUTER,"dst":DST})

    def test_distinct_vm_topology(self):
        vm.require_independent_vms(self.reports)

    def test_rejects_reused_machine_id_or_boot_id(self):
        self.reports["dst"]["machine_id"] = self.reports["src"]["machine_id"]
        with self.assertRaises(ValueError):
            vm.require_independent_vms(self.reports)
        self.reports = {"src":SRC,"router":ROUTER,"dst":dict(DST,boot_id=SRC["boot_id"])}
        with self.assertRaises(ValueError):
            vm.require_independent_vms(self.reports)

    def test_rejects_non_virtualbox_guest(self):
        self.reports["router"]["virtualization"]="kvm"
        with self.assertRaises(ValueError):
            vm.require_independent_vms(self.reports)

    def test_rejects_wrong_role_or_unrecognized_lab(self):
        self.reports["router"]["lab"]="any-cluster"
        with self.assertRaises(ValueError):
            vm.require_independent_vms(self.reports)
        self.reports["router"]["lab"]="sre-lab03d-vbox-intnet"
        self.reports["src"]["role"]="dst"
        with self.assertRaises(ValueError):
            vm.require_independent_vms(self.reports)

    def test_rejects_missing_router_link_or_ipv4_forwarding(self):
        del self.reports["router"]["links"]["enp0s9"]
        with self.assertRaises(ValueError):
            vm.require_independent_vms(self.reports)
        self.reports = {"src":SRC, "router":dict(ROUTER,ip_forward="0"), "dst":DST}
        with self.assertRaises(ValueError):
            vm.require_independent_vms(self.reports)

    def test_rejects_route_bypass_or_management_interface(self):
        self.reports["src"]["route"]["gateway"]="10.209.10.254"
        with self.assertRaises(ValueError):
            vm.require_independent_vms(self.reports)
        self.reports = {"src":dict(SRC,default_iface="enp0s8"),"router":ROUTER,"dst":DST}
        with self.assertRaises(ValueError):
            vm.require_independent_vms(self.reports)

    def test_rejects_same_link_or_synthetic_shared_namespace(self):
        self.reports["dst"]["links"]["enp0s9"]="10.209.10.2"
        with self.assertRaises(ValueError):
            vm.require_independent_vms(self.reports)

    def test_attestation_line_parser_requires_one_exact_report(self):
        line='noise\nSRE_LAB_ATTEST={"role":"src","lab":"sre-lab03d-vbox-intnet"}\n'
        self.assertEqual(vm.parse_attestation(line)["role"],"src")
        with self.assertRaises(ValueError):
            vm.parse_attestation(line+line)
        with self.assertRaises(ValueError):
            vm.parse_attestation("noise only")

    def test_http_results_require_real_body_proof(self):
        vm.require_http_proof({"health":{"ok":True,"status":200,"body":"healthy"},
            "upload":{"ok":True,"status":200,"digest":"a"*64}}, "a"*64)
        with self.assertRaises(ValueError):
            vm.require_http_proof({"health":{"ok":True,"status":200,"body":"healthy"},
                "upload":{"ok":True,"status":200,"digest":"b"*64}}, "a"*64)
        with self.assertRaises(ValueError):
            vm.require_http_proof({"health":{"ok":True,"status":200,"body":"healthy"},
                "upload":{"ok":False,"error":"timeout"}}, "a"*64)

    def test_explicit_run_id_rejects_path_traversal(self):
        for val in ["../bad","pmtu test","", "/tmp/x"]:
            with self.assertRaises(ValueError):
                vm.require_run_id(val)
        vm.require_run_id("lab03d-20261121T120000")

    def test_vagrant_internal_network_only(self):
        file = (ROOT / "environments/vm/Vagrantfile").read_text()
        self.assertEqual(file.count('virtualbox__intnet:'),4)
        self.assertNotIn('public_network',file)
        for ip in ("10.209.10.2","10.209.10.1","10.209.20.1","10.209.20.2"):
            self.assertIn(ip,file)


if __name__ == "__main__":
    unittest.main()
