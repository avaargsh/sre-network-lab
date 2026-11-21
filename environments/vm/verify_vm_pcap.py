#!/usr/bin/env python3
"""Independent classic-PCAP parser and four-point TCP baseline evidence auditor.

Requires the same source→destination TCP SYN and actual TCP request payload
on src, Router left, Router right and dst captures. Never treats a checksum
or a textual PASS status as proof of a real VirtualBox execution.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import socket
import struct
import sys

from http_vm import SHA256
from lab_vm import require_http_proof, require_independent_vms

SRC, DST, PORT = "10.209.10.2", "10.209.20.2", 18080
CAPTURES = ("src", "router-left", "router-right", "dst")
FILES = {"topology.json", "http.json", "capture-metadata.json", "status.json"} | {
    f"{s}.pcap" for s in CAPTURES
}
MAGIC = {
    b"\xd4\xc3\xb2\xa1": "<", b"\xa1\xb2\xc3\xd4": ">",
}


def decode_pcap(data: bytes) -> list[dict]:
    if len(data) < 24 or data[:4] not in MAGIC:
        raise ValueError("missing or unsupported classic PCAP header")
    endian = MAGIC[data[:4]]
    _, major, minor, _, _, snaplen, linktype = struct.unpack(endian + "IHHIIII", data[:24])
    if (major != 2 or minor != 4 or linktype != 1
            or not 64 <= snaplen <= 65535):
        raise ValueError("unsupported PCAP version, snaplen or Ethernet linktype")
    packets = []
    at = 24
    while at < len(data):
        if len(data) - at < 16:
            raise ValueError("truncated PCAP record header")
        _, _, captured, original = struct.unpack(endian + "IIII", data[at:at + 16])
        at += 16
        if (captured == 0 or captured > snaplen or original < captured
                or captured > len(data) - at):
            raise ValueError("invalid/truncated PCAP record payload")
        frame = data[at:at + captured]
        at += captured
        if len(frame) < 14 or frame[12:14] != b"\x08\x00":
            continue
        ip = frame[14:]
        if len(ip) < 20 or (ip[0] >> 4) != 4:
            continue
        ihl = (ip[0] & 0xF) * 4
        if ihl < 20 or len(ip) < ihl + 20 or ip[9] != 6:
            continue
        # IP fragments are not complete TCP headers; never use them for proof.
        frag_field = struct.unpack("!H", ip[6:8])[0]
        if frag_field & 0x1FFF:
            continue
        total_len = struct.unpack("!H", ip[2:4])[0]
        tcp = ip[ihl:]
        offset = (tcp[12] >> 4) * 4
        if offset < 20 or total_len < ihl + offset or len(tcp) < offset:
            continue
        sport, dport, seq = struct.unpack("!HHI", tcp[:8])
        packets.append({
            "src": socket.inet_ntoa(ip[12:16]),
            "dst": socket.inet_ntoa(ip[16:20]),
            "sport": sport, "dport": dport, "seq": seq,
            "flags": tcp[13],
            "payload_len": total_len - ihl - offset,
            # tcpdump snaplen=160 captures the request prefix, not the
            # complete wire segment. Keep only bounded header bytes.
            "payload_prefix": tcp[offset:offset + min(96, total_len - ihl - offset)],
        })
    if at != len(data):
        raise ValueError("unconsumed PCAP bytes")
    return packets


def _merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[list[int]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def _overlap(a: list[tuple[int, int]], b: list[tuple[int, int]]) -> list[tuple[int, int]]:
    a, b = _merge_intervals(a), _merge_intervals(b)
    i = j = 0
    output = []
    while i < len(a) and j < len(b):
        left, right = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if right > left:
            output.append((left, right))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return _merge_intervals(output)


def _body_intervals(packets: list[dict], sport: int, post_seq: int) -> list[tuple[int, int]]:
    result = []
    for packet in packets:
        if (packet["src"] != SRC or packet["dst"] != DST
                or packet["sport"] != sport or packet["dport"] != PORT
                or packet["payload_len"] < 512):
            continue
        # TCP sequence is modulo 2**32. This fixed 64-KiB lab must stay
        # close to POST's initial sequence; distant/old flows are excluded.
        delta = (packet["seq"] - post_seq) % (2 ** 32)
        if 0 < delta < 66000 and delta + packet["payload_len"] <= 66000:
            result.append((delta, delta + packet["payload_len"]))
    return _merge_intervals(result)


def require_four_point_flow(captures: dict[str, list[dict]]) -> dict:
    if set(captures) != set(CAPTURES):
        raise ValueError("must have exactly four guest NIC captures")
    syn_keys = None
    for side in CAPTURES:
        syns = {
            (p["sport"], p["seq"]) for p in captures[side]
            if p["src"] == SRC and p["dst"] == DST and p["dport"] == PORT
            and p["flags"] & 2 and not p["flags"] & 16
        }
        syn_keys = syns if syn_keys is None else syn_keys.intersection(syns)
    if not syn_keys:
        raise ValueError("no common source TCP SYN at all four private NICs")

    post_prefix = b"POST /upload HTTP/1."
    for sport, syn_seq in sorted(syn_keys):
        post_seq = (syn_seq + 1) % (2 ** 32)
        if not all(any(
                p["src"] == SRC and p["dst"] == DST
                and p["sport"] == sport and p["dport"] == PORT
                and p["seq"] == post_seq
                and p["payload_prefix"].startswith(post_prefix)
                for p in captures[side]) for side in CAPTURES):
            continue

        common = _body_intervals(captures["src"], sport, post_seq)
        for side in CAPTURES[1:]:
            common = _overlap(common, _body_intervals(captures[side], sport, post_seq))
            if not common:
                break
        common_body_bytes = sum(end - start for start, end in common)
        if common_body_bytes >= 512:
            return {
                "status": "PASS", "source_port": sport,
                "syn_sequence": syn_seq, "post_sequence": post_seq,
                "common_body_bytes": common_body_bytes,
            }
    raise ValueError(
        "no shared POST /upload request and >=512 forwarded TCP body bytes "
        "at all four private NICs"
    )

def verify(root: Path, expected_digest: str = SHA256) -> dict:
    if not root.is_dir() or root.is_symlink():
        raise ValueError("missing/unsafe lab evidence directory")
    entries = list(root.iterdir())
    if any(not p.is_file() or p.is_symlink() for p in entries):
        raise ValueError("evidence contains unsafe non-regular files")
    if {p.name for p in entries} != FILES | {"SHA256SUMS"}:
        raise ValueError("missing or extra evidence files")
    checksums = {}
    for line in (root / "SHA256SUMS").read_text().splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9_.-]+)", line)
        if not match or match[2] in checksums:
            raise ValueError("invalid or duplicated manifest record")
        checksums[match[2]] = match[1]
    if set(checksums) != FILES:
        raise ValueError("hashes do not cover exactly the evidence set")
    for name, sha in checksums.items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != sha:
            raise ValueError(f"evidence SHA-256 mismatch: {name}")

    status = json.loads((root / "status.json").read_text())
    expected_status = {
        "result": "PASS", "scope": "3 independent VirtualBox VM NICs / 4 TCP captures",
        "pcap": "PASS", "http": "PASS",
        "mtu_fault": "NOT_VERIFIED", "tcp_blackhole": "NOT_VERIFIED",
    }
    if status != expected_status:
        raise ValueError("unverified fault/blackhole must remain NOT_VERIFIED")
    require_independent_vms(json.loads((root / "topology.json").read_text()))
    require_http_proof(json.loads((root / "http.json").read_text()), expected_digest)
    meta = json.loads((root / "capture-metadata.json").read_text())
    expected_sides = {
        "src": ("src", "10.209.10.2"),
        "router-left": ("router", "10.209.10.1"),
        "router-right": ("router", "10.209.20.1"),
        "dst": ("dst", "10.209.20.2"),
    }
    if set(meta) != set(expected_sides):
        raise ValueError("capture point metadata incomplete")
    topology = json.loads((root / "topology.json").read_text())
    decoded = {}
    for side, (role, ip) in expected_sides.items():
        details = meta[side]
        iface = details.get("interface")
        if (details.get("role") != role or details.get("private_ip") != ip
                or not isinstance(iface, str)
                or topology[role]["links"].get(iface) != ip):
            raise ValueError(f"{side}: capture came from wrong VM private interface")
        contents = (root / (side + ".pcap")).read_bytes()
        if details.get("size") != len(contents) or \
                details.get("sha256") != hashlib.sha256(contents).hexdigest():
            raise ValueError(f"{side}: capture receipt differs from raw bytes")
        decoded[side] = decode_pcap(contents)
    correlation = require_four_point_flow(decoded)
    return {"status": "PASS", "files_hashed": len(checksums),
            "pcap": "PASS", "http": "PASS", "flow": correlation,
            "mtu_fault": "NOT_VERIFIED", "tcp_blackhole": "NOT_VERIFIED"}


if __name__ == "__main__":
    try:
        if len(sys.argv) != 2:
            raise ValueError("usage: verify_vm_pcap.py <evidence-directory>")
        print(json.dumps(verify(Path(sys.argv[1])), indent=2))
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        print("NOT_VERIFIED: " + str(exc), file=sys.stderr)
        sys.exit(1)
