"""TCP PMTU lab contracts. These are offline tests, not evidence of real packets."""
import importlib.util
from pathlib import Path
import unittest
import sys

TARGET = Path(__file__).resolve().parents[1] / "scripts" / "lab_pmtu_tcp.py"
sys.path.insert(0, str(TARGET.parent))
spec = importlib.util.spec_from_file_location("lab_pmtu_tcp", TARGET)
lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab)

SRC = "10.207.10.2"
DST = "10.207.20.2"
SYN = "1760.01 IP 10.207.10.2.45322 > 10.207.20.2.18080: Flags [S], seq 987, win 64240, options [mss 1460,sackOK], length 0"
SYNACK = "1760.02 IP 10.207.20.2.18080 > 10.207.10.2.45322: Flags [S.], seq 100, ack 988, win 65160, options [mss 1160,sackOK], length 0"
DATA = "1760.03 IP 10.207.10.2.45322 > 10.207.20.2.18080: Flags [P.], seq 1:1449, ack 1, win 512, length 1448"
RETX = "1761.03 IP 10.207.10.2.45322 > 10.207.20.2.18080: Flags [P.], seq 1:1449, ack 1, win 512, length 1448"
SMALL = "1760.04 IP 10.207.10.2.45325 > 10.207.20.2.18080: Flags [P.], seq 1:1001, ack 1, win 512, length 1000"
MITIGATED = "1760.04 IP 10.207.10.2.45322 > 10.207.20.2.18080: Flags [P.], seq 1:1161, ack 1, win 512, length 1160"


class TCPPMTUContracts(unittest.TestCase):
    def test_parses_data_from_only_requested_tcp_flow(self):
        packets = lab.parse_tcpdump("\n".join([SYN, SYNACK, DATA, RETX, SMALL, "not tcp"]))
        self.assertEqual(len(packets), 5)
        self.assertEqual(lab.max_payload(packets, SRC, DST), 1448)
        self.assertEqual(lab.max_payload(packets, DST, SRC), 0)
        self.assertEqual(lab.count_retransmissions(packets, SRC, DST), 1)
        self.assertEqual(lab.synack_mss(packets), 1160)

    def test_refuses_unrelated_tcp_data_as_business_proof(self):
        fake = "1760.2 IP 10.207.10.2.45322 > 10.207.20.9.18080: Flags [P.], seq 1:2001, ack 1, length 2000"
        self.assertEqual(lab.max_payload(lab.parse_tcpdump(fake), SRC, DST), 0)
        self.assertEqual(lab.count_retransmissions(lab.parse_tcpdump(fake), SRC, DST), 0)

    def test_blackhole_requires_connection_failure_and_retransmission(self):
        i = lab.parse_tcpdump("\n".join([SYN, SYNACK, DATA, RETX, SMALL]))
        o = lab.parse_tcpdump("\n".join([SYN, SYNACK, SMALL]))
        lab.assert_phase("blackhole", {"health": {"ok": True},
                     "upload": {"ok": False, "error": "timeout"}}, i, o)
        with self.assertRaises(ValueError):
            lab.assert_phase("blackhole", {"health": {"ok": True},
                     "upload": {"ok": True}}, i, o)
        with self.assertRaises(ValueError):
            lab.assert_phase("blackhole", {"health": {"ok": True},
                     "upload": {"ok": False, "error": "timeout"}}, i, i)

    def test_mitigation_requires_actual_mss_and_large_upload(self):
        i = lab.parse_tcpdump("\n".join([SYN, SYNACK, MITIGATED]))
        o = lab.parse_tcpdump("\n".join([SYN, SYNACK, MITIGATED]))
        lab.assert_phase("mitigated", {"health": {"ok": True},
                         "upload": {"ok": True}}, i, o)
        with self.assertRaises(ValueError):
            lab.assert_phase("mitigated", {"health": {"ok": True},
                             "upload": {"ok": True}}, i,
                             lab.parse_tcpdump("\n".join([SYN, SYNACK, DATA])))

    def test_baseline_and_recovery_need_large_tcp_segments_both_interfaces(self):
        p = lab.parse_tcpdump("\n".join([SYN, SYNACK, DATA]))
        for stage in ("baseline", "recovery"):
            lab.assert_phase(stage, {"upload": {"ok": True}}, p, p)
            with self.assertRaises(ValueError):
                lab.assert_phase(stage, {"upload": {"ok": True}}, p,
                                 lab.parse_tcpdump(SYN + "\n" + SYNACK))

    def test_mss_must_appear_on_synack(self):
        p = lab.parse_tcpdump(SYN + "\n" + DATA)
        self.assertIsNone(lab.synack_mss(p))
        with self.assertRaises(ValueError):
            lab.assert_phase("mitigated", {"upload": {"ok": True}}, p, p)

    def test_undocumented_phase_fails_closed(self):
        with self.assertRaises(ValueError):
            lab.assert_phase("ignored", {"upload": {"ok": True}}, [], [])


if __name__ == "__main__":
    unittest.main()
