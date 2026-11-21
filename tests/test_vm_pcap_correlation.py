"""LAB-03D: prevent four-NIC PCAP false positives from unrelated TCP traffic.

Fixtures are synthetic Ethernet/IP/TCP records; not a claim of real VM E2E.
"""
import importlib.util
from pathlib import Path
import socket
import struct
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"environments"/"vm"))
from test_vm_pcap import frame
spec=importlib.util.spec_from_file_location(
    "verify_vm_pcap",ROOT/"environments"/"vm"/"verify_vm_pcap.py")
pcap=importlib.util.module_from_spec(spec)
spec.loader.exec_module(pcap)

POST=b"POST /upload HTTP/1.1\r\nHost: 10.209.20.2\r\nContent-Length: 65536\r\n\r\n"
GET=b"GET /health HTTP/1.1\r\nHost: 10.209.20.2\r\n\r\n"
ISN=99
SPORT=45000

def captured(*raw_frames):
    """Pcap snaplen=160: original IP length is preserved in truncated records."""
    output=bytearray(struct.pack("<IHHIIII",0xa1b2c3d4,2,4,0,0,160,1))
    for raw in raw_frames:
        packet=raw[:160]
        output+=struct.pack("<IIII",123,0,len(packet),len(raw))+packet
    return pcap.decode_pcap(bytes(output))

def flow(method=POST, body=True, body_seq=None, segments=(1400,), port=SPORT):
    data=[frame(seq=ISN,sport=port,flags=2),
          frame(seq=ISN+1,sport=port,flags=24,payload=method)]
    if body:
        seq=body_seq if body_seq is not None else ISN+1+len(method)
        for length in segments:
            data.append(frame(seq=seq,sport=port,flags=24,payload=b"x"*length))
            seq+=length
    return captured(*data)

class StrictForwardedHTTP(unittest.TestCase):
    def test_rejects_health_only_even_if_every_interface_has_same_syn_and_payload(self):
        all_sides={name:flow(GET) for name in pcap.CAPTURES}
        with self.assertRaises(ValueError):
            pcap.require_four_point_flow(all_sides)

    def test_rejects_missing_post_request_on_router_egress(self):
        all_sides={name:flow() for name in pcap.CAPTURES}
        all_sides["router-right"]=flow(GET)
        with self.assertRaises(ValueError):
            pcap.require_four_point_flow(all_sides)

    def test_rejects_post_header_different_tcp_sequence_on_dst(self):
        all_sides={name:flow() for name in pcap.CAPTURES}
        # Same handshake and same port, but dst PCAP has no common POST seq.
        abnormal=captured(
            frame(seq=ISN,flags=2),
            frame(seq=ISN+500,flags=24,payload=POST),
            frame(seq=ISN+1+len(POST),flags=24,payload=b"x"*1400))
        all_sides["dst"]=abnormal
        with self.assertRaises(ValueError):
            pcap.require_four_point_flow(all_sides)

    def test_rejects_no_large_upload_body_on_router_egress(self):
        all_sides={name:flow() for name in pcap.CAPTURES}
        all_sides["router-right"]=flow(body=False)
        with self.assertRaises(ValueError):
            pcap.require_four_point_flow(all_sides)

    def test_rejects_unrelated_body_seq_outside_original_flow(self):
        all_sides={name:flow() for name in pcap.CAPTURES}
        all_sides["dst"]=flow(body_seq=ISN+200000)
        with self.assertRaises(ValueError):
            pcap.require_four_point_flow(all_sides)

    def test_accepts_same_post_and_overlapping_body_bytes_when_router_segments(self):
        all_sides={
            "src":flow(segments=(1400,)),
            "router-left":flow(segments=(1400,)),
            "router-right":flow(segments=(700,700)),
            "dst":flow(segments=(700,700)),
        }
        result=pcap.require_four_point_flow(all_sides)
        self.assertEqual(result["source_port"],SPORT)
        self.assertGreaterEqual(result["common_body_bytes"],512)

    def test_truncated_pcap_record_payload_is_parsed_using_wire_ip_length(self):
        packets=flow()
        bodies=[p for p in packets if p["payload_len"]>500]
        self.assertEqual(len(bodies),1)
        self.assertEqual(bodies[0]["payload_len"],1400)
        # Only a prefix is captured: cannot equate captured length with wire length.
        self.assertLess(len(bodies[0]["payload_prefix"]),1400)

if __name__=="__main__":
    unittest.main()
