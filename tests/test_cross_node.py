"""Small contract tests for LAB-03; real Calico E2E is a separate CI job."""
import importlib.util
import json
import pathlib
import unittest

MODULE = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "lab_cross_node.py"
spec = importlib.util.spec_from_file_location("lab_cross_node", MODULE)
lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab)

def node(name, ip, ready=True, control=False):
    labels = {"node-role.kubernetes.io/control-plane": ""} if control else {}
    return {
        "metadata": {"name": name, "labels": labels},
        "status": {
            "conditions": [{"type": "Ready", "status": "True" if ready else "False"}],
            "addresses": [{"type": "InternalIP", "address": ip}],
        },
    }

DECODED = """
1710000000.300000 IP (tos 0x0, ttl 64) 172.18.0.3.51488 > 172.18.0.4.4789: VXLAN, flags [I], vni 4096
    IP (tos 0x0, ttl 63) 192.168.1.8.33221 > 192.168.2.9.5678: Flags [P.], length 143
    ....GET /?proof=crossnode-test-1 HTTP/1.1
1710000000.500000 IP (tos 0x0, ttl 64) 172.18.0.3.51488 > 172.18.0.4.4789: VXLAN, flags [I], vni 4096
    IP 192.168.1.8.33221 > 192.168.2.9.5678: Flags [P.]
    ....GET /health HTTP/1.1
"""

class CrossNodeContracts(unittest.TestCase):
    def test_selects_two_distinct_ready_workers(self):
        nodes = {"items": [
            node("control", "172.18.0.2", control=True),
            node("worker-b", "172.18.0.4"),
            node("worker-a", "172.18.0.3"),
            node("notready", "172.18.0.5", ready=False),
        ]}
        src, dst = lab.select_workers(nodes)
        self.assertEqual(src["name"], "worker-a")
        self.assertEqual(dst["name"], "worker-b")
        self.assertNotEqual(src["ip"], dst["ip"])

    def test_fails_when_two_workers_not_ready(self):
        with self.assertRaises(ValueError):
            lab.select_workers({"items": [node("worker-a", "172.18.0.3"),
                                          node("worker-b", "172.18.0.4", ready=False)]})

    def test_fails_if_worker_node_ips_overlap(self):
        with self.assertRaises(ValueError):
            lab.select_workers({"items": [node("a", "172.18.0.3"), node("b", "172.18.0.3")]})

    def test_context_gate_fail_closed(self):
        lab.require_mutation_approval("kind-sre-policy-lab", "kind-sre-policy-lab", "1")
        with self.assertRaises(ValueError):
            lab.require_mutation_approval("production", "kind-sre-policy-lab", "1")
        with self.assertRaises(ValueError):
            lab.require_mutation_approval("kind-sre-policy-lab", "kind-sre-policy-lab", "0")

    def test_calico_ip_pool_must_be_vxlan_always(self):
        ok={"items":[{"spec":{"cidr":"192.168.0.0/16","vxlanMode":"Always","ipipMode":"Never"}}]}
        lab.require_vxlan_pool(ok)
        with self.assertRaises(ValueError):
            lab.require_vxlan_pool({"items":[{"spec":{"vxlanMode":"Never","ipipMode":"Always"}}]})

    def test_pod_nodes_must_match_pinned_workers(self):
        lab.require_pinned_nodes("worker-a","worker-b","worker-a","worker-b")
        with self.assertRaises(ValueError):
            lab.require_pinned_nodes("worker-a","worker-a","worker-a","worker-b")
        with self.assertRaises(ValueError):
            lab.require_pinned_nodes("worker-c","worker-b","worker-a","worker-b")

    def test_vxlan_packet_contains_same_request_proof(self):
        self.assertTrue(lab.has_vxlan_proof(DECODED,"172.18.0.3","172.18.0.4",
                                             "192.168.1.8","192.168.2.9","crossnode-test-1"))
        self.assertFalse(lab.has_vxlan_proof(DECODED,"172.18.0.4","172.18.0.3",
                                              "192.168.1.8","192.168.2.9","crossnode-test-1"))
        self.assertFalse(lab.has_vxlan_proof(DECODED,"172.18.0.3","172.18.0.4",
                                              "192.168.1.8","192.168.2.9","wrong-id"))

    def test_vxlan_proof_must_be_same_packet(self):
        wrong = """
1710000000.1 IP 172.18.0.3.33000 > 172.18.0.4.4789: VXLAN, vni 1
    IP 192.168.1.8.123 > 192.168.2.9.5678: Flags [P.]
1710000000.2 IP 172.18.0.3.33000 > 172.18.0.4.4789: VXLAN, vni 1
    IP 192.168.3.1.123 > 192.168.4.1.5678: Flags [P.]
    ...GET /?proof=crossnode-test-1 HTTP/1.1
"""
        self.assertFalse(lab.has_vxlan_proof(wrong,"172.18.0.3","172.18.0.4",
                                              "192.168.1.8","192.168.2.9","crossnode-test-1"))

    def test_template_rejects_untrusted_node_name(self):
        with self.assertRaises(ValueError):
            lab.render_manifest("nodeName: __SOURCE_NODE__", "a; touch /tmp/hijack", "worker-b")
        self.assertEqual(lab.render_manifest("a=__SOURCE_NODE__,b=__DEST_NODE__","worker-a","worker-b"),
                         "a=worker-a,b=worker-b")

if __name__=="__main__":
    unittest.main()
