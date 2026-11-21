"""LAB-03B PMTU fixture contracts; this test does NOT require CAP_NET_ADMIN."""
import importlib.util
import pathlib
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "lab_pmtu.py"
spec = importlib.util.spec_from_file_location("lab_pmtu", SCRIPT)
lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab)

BIG = "10.207.10.2 > 10.207.20.2: ICMP echo request, id 42, seq 1, length 1408"
SMALL = "10.207.10.2 > 10.207.20.2: ICMP echo request, id 42, seq 1, length 908"
FRAG = "10.207.10.1 > 10.207.10.2: ICMP 10.207.20.2 unreachable - need to frag (mtu 1200), length 556"


class PMTUContractTests(unittest.TestCase):
    def test_refuses_unapproved_host(self):
        lab.require_approval("1", "dedicated-netns", 0)
        with self.assertRaises(ValueError):
            lab.require_approval("0", "dedicated-netns", 0)
        with self.assertRaises(ValueError):
            lab.require_approval("1", "production-host", 0)
        with self.assertRaises(ValueError):
            lab.require_approval("1", "dedicated-netns", 1000)

    def test_names_are_reserved(self):
        self.assertEqual(lab.NAMES, ("sre-pmtu-src", "sre-pmtu-hop", "sre-pmtu-dst"))

    def test_discovery_allows_empty_egress_pcap_header_only(self):
        self.assertTrue(lab.valid_pcap_size("discovery", "egress", 24))
        self.assertFalse(lab.valid_pcap_size("discovery", "egress", 23))
        self.assertFalse(lab.valid_pcap_size("blackhole", "egress", 24))
        self.assertFalse(lab.valid_pcap_size("discovery", "ingress", 24))
        self.assertTrue(lab.valid_pcap_size("discovery", "ingress", 150))

    def test_large_packet_matching_is_exact(self):
        self.assertTrue(lab.has_echo(BIG, 1400))
        self.assertTrue(lab.has_echo(SMALL, 900))
        self.assertFalse(lab.has_echo(SMALL, 1400))
        self.assertFalse(lab.has_echo("10.207.1.1 > 10.207.20.2: ICMP echo request, length 1408", 1400))
        self.assertFalse(lab.has_echo("10.207.10.2 > 10.207.22.2: ICMP echo request, length 1408", 1400))

    def test_fragmentation_needed_requires_real_icmp_from_router(self):
        self.assertTrue(lab.has_frag_needed(FRAG))
        self.assertFalse(lab.has_frag_needed("10.207.10.1 > 10.207.10.2: ICMP echo reply"))
        self.assertFalse(lab.has_frag_needed("10.207.20.2 > 10.207.10.2: ICMP unreachable - need to frag"))

    def test_phase_checks_prevent_false_blackhole(self):
        lab.check_phase(
            "blackhole",
            {"small": (0, "2 received"), "large": (1, "2 transmitted, 0 received, 100% packet loss")},
            BIG + "\n" + SMALL, SMALL
        )
        with self.assertRaises(ValueError):
            lab.check_phase(
                "blackhole",
                {"small": (0, "2 received"), "large": (1, "Frag needed and DF set")},
                BIG + "\n" + FRAG, SMALL
            )
        with self.assertRaises(ValueError):
            lab.check_phase(
                "blackhole",
                {"small": (0, "2 received"), "large": (1, "100% packet loss")},
                BIG + "\n" + SMALL, BIG + "\n" + SMALL
            )

    def test_recovery_needs_large_packets_on_both_interfaces(self):
        lab.check_phase("recovery", {"large": (0, "2 received")}, BIG, BIG)
        with self.assertRaises(ValueError):
            lab.check_phase("recovery", {"large": (0, "2 received")}, BIG, SMALL)

    def test_discovery_needs_actual_icmp_and_large_is_not_delivered(self):
        lab.check_phase("discovery", {"large": (1, "Frag needed and DF set")}, BIG + "\n" + FRAG, "")
        with self.assertRaises(ValueError):
            lab.check_phase("discovery", {"large": (1, "Frag needed and DF set")}, BIG, "")
        with self.assertRaises(ValueError):
            lab.check_phase("discovery", {"large": (0, "2 received")}, BIG + "\n" + FRAG, "")

    def test_baseline_requires_large_success_and_cross_router_forwarding(self):
        lab.check_phase("baseline", {"large": (0, "2 received")}, BIG, BIG)
        with self.assertRaises(ValueError):
            lab.check_phase("baseline", {"large": (0, "2 received")}, BIG, SMALL)


if __name__ == "__main__":
    unittest.main()
