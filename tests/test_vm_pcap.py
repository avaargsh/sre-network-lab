"""LAB-03D four-point PCAP baseline: fail-closed capture and packet contracts."""
import importlib.util
from pathlib import Path
import socket
import struct
import sys
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
VM=ROOT/"environments"/"vm"
sys.path.insert(0,str(VM))
spec=importlib.util.spec_from_file_location("pcap_vm",VM/"pcap_vm.py")
p=importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
s2=importlib.util.spec_from_file_location("verify_vm_pcap",VM/"verify_vm_pcap.py")
v=importlib.util.module_from_spec(s2)
s2.loader.exec_module(v)

def frame(src="10.209.10.2",dst="10.209.20.2",sport=45000,
          dport=18080,seq=99,flags=2,payload=b""):
    tcp=struct.pack("!HHIIHHHH",sport,dport,seq,0,(5<<12)|flags,4096,0,0)+payload
    ip=struct.pack("!BBHHHBBH4s4s",0x45,0,len(tcp)+20,0,0,64,6,0,
                   socket.inet_aton(src),socket.inet_aton(dst))
    eth=b"\x00"*12+b"\x08\x00"
    return eth+ip+tcp

def packet_file(*frames):
    out=bytearray(struct.pack("<IHHIIII",0xa1b2c3d4,2,4,0,0,160,1))
    for f in frames:
        out.extend(struct.pack("<IIII",123,0,len(f),len(f)))
        out.extend(f)
    return bytes(out)

class CaptureSafety(unittest.TestCase):
    def test_fixed_four_interfaces_only(self):
        self.assertEqual(p.sides("src"),("src",))
        self.assertEqual(p.sides("router"),("left","right"))
        self.assertEqual(p.sides("dst"),("dst",))
        with self.assertRaises(ValueError):
            p.sides("host")

    def test_rejects_default_nic_and_foreign_interface(self):
        c={"role":"router","default_iface":"enp0s3",
           "links":{"enp0s8":"10.209.10.1","enp0s9":"10.209.20.1"}}
        self.assertEqual(p.choose_interfaces("router",c),{"left":"enp0s8","right":"enp0s9"})
        c["default_iface"]="enp0s9"
        with self.assertRaises(ValueError):
            p.choose_interfaces("router",c)
        c["default_iface"]="enp0s3"
        c["links"]["enp0s9"]="10.209.99.1"
        with self.assertRaises(ValueError):
            p.choose_interfaces("router",c)

    def test_only_fixed_tcp_bpf_and_bounded_capture(self):
        a=p.capture_command("safe-run01","left","enp0s8")
        self.assertEqual(a[0],"systemd-run")
        self.assertIn("--property=RuntimeMaxSec=120s",a)
        self.assertIn("-c",a)
        self.assertIn("512",a)
        self.assertIn("18080",a)
        self.assertNotIn("enp0s3",str(a))

    def test_malformed_run_identifier_is_rejected(self):
        for r in ("../etc","a;b","", "foo bar","A"*40):
            with self.assertRaises(ValueError):
                p.validate_run_id(r)

    def test_bounded_export_refuses_empty_oversized(self):
        with self.assertRaises(ValueError):
            p.check_pcap_bytes(b"\x00"*24)
        with self.assertRaises(ValueError):
            p.check_pcap_bytes(b"\x00"*(p.MAX_PCAP_BYTES+1))
        self.assertTrue(p.check_pcap_bytes(packet_file(frame())))

class CaptureAudit(unittest.TestCase):
    def test_reads_real_ethernet_ipv4_tcp_packet(self):
        a=v.decode_pcap(packet_file(frame(),frame(flags=24,payload=b"abc")))
        self.assertEqual(a[0]["src"],"10.209.10.2")
        self.assertEqual(a[0]["sport"],45000)
        self.assertEqual(a[0]["flags"] & 2,2)
        self.assertEqual(a[1]["payload_len"],3)

    def test_rejects_truncated_or_wrong_linktype(self):
        good=packet_file(frame())
        for bad in (good[:10],good[:-3],good[:20]+b"\x02\x00\x00\x00"+good[24:]):
            with self.assertRaises(ValueError):
                v.decode_pcap(bad)

    def test_same_handshake_on_all_four_points_required(self):
        a=v.decode_pcap(packet_file(frame(),frame(flags=24,payload=b"real")))
        captures={"src":a,"router-left":a,"router-right":a,"dst":a}
        v.require_four_point_flow(captures)
        captures["dst"]=v.decode_pcap(packet_file(frame(sport=45001)))
        with self.assertRaises(ValueError):
            v.require_four_point_flow(captures)

    def test_router_outbound_tcp_data_required(self):
        syn=v.decode_pcap(packet_file(frame()))
        payload=v.decode_pcap(packet_file(frame(flags=24,payload=b"request")))
        captures={"src":syn+payload,"router-left":syn+payload,
                  "router-right":syn,"dst":syn+payload}
        with self.assertRaises(ValueError):
            v.require_four_point_flow(captures)

if __name__=="__main__":
    unittest.main()
