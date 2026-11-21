#!/usr/bin/env python3
"""LAB-03C: real TCP PMTU blackhole and MSS clamping on isolated Linux netns.

Never manipulates host routing, host firewall, or physical NICs.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

from lab_pmtu import (DST, SRC, HOP, NAMES, ROOT, NetnsFixture, command, checksum,
                      require_approval, run_ns)

ENDPOINT = ROOT / "scripts" / "pmtu_http_endpoint.py"
PORT = 18080
MSS = 1160
TCPMSS_RULE = ("-p", "tcp", "--tcp-flags", "SYN,RST", "SYN",
               "-j", "TCPMSS", "--set-mss", str(MSS))
PHASES = ("baseline", "blackhole", "mitigated", "recovery")

TCP_RE = re.compile(
    r"^\s*\d+(?:\.\d+)?\s+IP\s+"
    r"(?P<src>\d{1,3}(?:\.\d{1,3}){3})\.(?P<src_port>\d+)\s*>\s*"
    r"(?P<dst>\d{1,3}(?:\.\d{1,3}){3})\.(?P<dst_port>\d+):\s*"
    r"Flags\s*\[(?P<flags>[^\]]+)\],\s*(?P<rest>.+)$",
    re.MULTILINE
)
SRC_IP = "10.207.10.2"
DST_IP = "10.207.20.2"


def parse_tcpdump(decoded: str) -> list[dict]:
    packets = []
    for match in TCP_RE.finditer(decoded):
        rest = match["rest"]
        length = re.search(r"\blength\s+(\d+)\b", rest)
        seq = re.search(r"\bseq\s+(\d+)(?::\d+)?", rest)
        mss = re.search(r"\bmss\s+(\d+)\b", rest)
        packets.append({
            "src": match["src"], "dst": match["dst"],
            "src_port": int(match["src_port"]), "dst_port": int(match["dst_port"]),
            "flags": match["flags"], "length": int(length[1]) if length else -1,
            "seq": int(seq[1]) if seq else None,
            "mss": int(mss[1]) if mss else None
        })
    return packets


def data_packets(packets: list[dict], src: str = SRC_IP, dst: str = DST_IP) -> list[dict]:
    return [p for p in packets if p["src"] == src and p["dst"] == dst
            and p["dst_port"] == PORT and p["length"] > 0]


def max_payload(packets: list[dict], src: str = SRC_IP, dst: str = DST_IP) -> int:
    return max((p["length"] for p in data_packets(packets, src, dst)), default=0)


def count_retransmissions(packets: list[dict], src: str = SRC_IP, dst: str = DST_IP) -> int:
    seen: set[tuple[int, int, int]] = set()
    retrans = 0
    for p in data_packets(packets, src, dst):
        if p["seq"] is None or p["length"] <= 1100:
            continue
        key = (p["src_port"], p["seq"], p["length"])
        if key in seen:
            retrans += 1
        seen.add(key)
    return retrans


def synack_mss(packets: list[dict]) -> int | None:
    values = [p["mss"] for p in packets
              if p["src"] == DST_IP and p["dst"] == SRC_IP
              and p["src_port"] == PORT and "S" in p["flags"]
              and "." in p["flags"] and p["mss"] is not None]
    return min(values) if values else None


def assert_phase(phase: str, results: dict, ingress: list[dict], egress: list[dict]) -> None:
    if phase not in PHASES:
        raise ValueError(f"unrecognized phase: {phase}")
    if phase in ("baseline", "recovery"):
        if (not results.get("upload", {}).get("ok") or
                max_payload(ingress) <= 1200 or max_payload(egress) <= 1200):
            raise ValueError(f"{phase}: require real 64KiB HTTP upload and >1200 byte TCP data on both router interfaces")
    elif phase == "blackhole":
        if (not results.get("health", {}).get("ok") or
                results.get("upload", {}).get("ok") is not False or
                results.get("upload", {}).get("error") != "timeout" or
                max_payload(ingress) <= 1200 or
                max_payload(egress) > 1200 or max_payload(egress) == 0 or
                count_retransmissions(ingress) < 1):
            raise ValueError("blackhole: require healthy small HTTP, timeout of upload, ingress oversize TCP and retransmits, no oversize egress")
    else:
        mss = synack_mss(ingress)
        if (not results.get("health", {}).get("ok") or
                not results.get("upload", {}).get("ok") or
                mss is None or mss > MSS or
                max_payload(egress) < 1 or max_payload(egress) > MSS):
            raise ValueError(f"mitigated: require HTTP upload success, SYNACK MSS <= {MSS}, and egress TCP payload <= {MSS}")


def ns_client(kind: str) -> dict:
    p = run_ns(SRC, "python3", str(ENDPOINT), kind, check=False, timeout=18)
    try:
        result = json.loads(p.stdout.strip())
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed HTTP client result ({kind}): {p.stderr}") from exc
    if (bool(result.get("ok")) != (p.returncode == 0) or result.get("action") != kind):
        raise ValueError(f"HTTP client exit/result mismatch for {kind}")
    return result


def capture(phase: str, path: Path, operations: tuple[str, ...]) -> None:
    path.mkdir(parents=True, exist_ok=False)
    captures: list[tuple[str, subprocess.Popen, object]] = []
    try:
        for side, iface in (("ingress", "h0"), ("egress", "h1")):
            fd = (path / f"{side}.pcap").open("wb")
            proc = subprocess.Popen(
                ["ip", "netns", "exec", HOP, "tcpdump", "--immediate-mode", "-i", iface,
                 "-s", "0", "-n", "-U", "-w", "-", "tcp", "and", "port", str(PORT),
                 "and", "host", SRC_IP, "and", "host", DST_IP],
                stdout=fd, stderr=subprocess.PIPE, start_new_session=True
            )
            captures.append((side, proc, fd))
        time.sleep(0.8)
        if any(proc.poll() is not None for _, proc, _ in captures):
            raise RuntimeError("tcpdump exited before business probes")
        results = {kind: ns_client(kind) for kind in operations}
        (path / "http.json").write_text(json.dumps(results, indent=2) + "\n")
        # AF_PACKET capture may lag an extremely fast HTTP exchange.
        time.sleep(1.2)
    finally:
        for side, proc, fd in captures:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGINT)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=5)
            fd.close()
            (path / f"{side}.tcpdump.stderr").write_bytes(proc.stderr.read())
            proc.stderr.close()
    for side in ("ingress", "egress"):
        pcap = path / f"{side}.pcap"
        if pcap.stat().st_size <= 40:
            raise ValueError(f"{phase}: empty/truncated {side} PCAP")
        decoded = command("tcpdump", "-tt", "-nn", "-r", str(pcap)).stdout
        (path / f"{side}.decoded.txt").write_text(decoded)
    ingress = parse_tcpdump((path / "ingress.decoded.txt").read_text())
    egress = parse_tcpdump((path / "egress.decoded.txt").read_text())
    assert_phase(phase, results, ingress, egress)
    (path / "status.json").write_text(json.dumps({
        "phase": phase, "status": "PASS", "time": datetime.now(timezone.utc).isoformat(),
        "max_ingress_payload": max_payload(ingress),
        "max_egress_payload": max_payload(egress),
        "retransmissions": count_retransmissions(ingress),
        "synack_mss": synack_mss(ingress)
    }, indent=2) + "\n")
    checksum(path)
    print(f"PASS TCP {phase}: in={max_payload(ingress)} out={max_payload(egress)} "
          f"retrans={count_retransmissions(ingress)} mss={synack_mss(ingress)}", flush=True)


def mss_rule(enabled: bool) -> None:
    if enabled:
        run_ns(HOP, "iptables", "-t", "mangle", "-I", "FORWARD", "1", *TCPMSS_RULE)
    else:
        has = run_ns(HOP, "iptables", "-t", "mangle", "-C", "FORWARD",
                     *TCPMSS_RULE, check=False)
        if has.returncode == 0:
            run_ns(HOP, "iptables", "-t", "mangle", "-D", "FORWARD", *TCPMSS_RULE)


def no_offload() -> None:
    if not shutil.which("ethtool"):
        raise RuntimeError("ethtool needed for packet-size evidence (GRO/GSO/TSO)")
    for ns, iface in ((SRC, "eth0"), (HOP, "h0"), (HOP, "h1"), (DST, "eth0")):
        run_ns(ns, "ethtool", "-K", iface, "tso", "off", "gso", "off", "gro", "off")
    run_ns(SRC, "sysctl", "-w", "net.ipv4.tcp_mtu_probing=0")


def main() -> int:
    try:
        require_approval(os.getenv("SRE_LAB_ALLOW_MUTATIONS", ""),
                         os.getenv("SRE_LAB_SCOPE", ""), os.geteuid())
        run_id = os.getenv("RUN_ID", "")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,75}", run_id):
            raise ValueError("explicit safe RUN_ID required")
        root = Path(os.getenv("EVIDENCE_DIR", str(ROOT / "evidence"))) / run_id
        root.mkdir(parents=True, exist_ok=False, mode=0o700)
        fixture = NetnsFixture(root)
    except (OSError, ValueError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3

    status = "FAILED"
    server = None
    logs = None
    try:
        fixture.setup()
        no_offload()
        logfile = root / "http-server.stderr"
        logs = logfile.open("wb")
        server = subprocess.Popen(["ip", "netns", "exec", DST, "python3",
                                   str(ENDPOINT), "serve"], stdout=subprocess.DEVNULL,
                                  stderr=logs, start_new_session=True)
        time.sleep(0.5)
        if server.poll() is not None:
            raise RuntimeError("HTTP echo server failed to start")
        (root / "environment.json").write_text(json.dumps({
            "kernel": command("uname", "-r").stdout.strip(),
            "run_id": run_id,
            "scope": "one isolated runner with three Linux namespaces, NOT separate VMs",
            "topology": "src 10.207.10.2 -> router h0/h1 -> dst 10.207.20.2:18080",
            "original_mtu": 1500, "bottleneck_mtu": 1200, "clamp_mss": MSS
        }, indent=2) + "\n")

        capture("baseline", root / "baseline", ("upload",))
        fixture.mtulimit(1200)
        fixture.drop_frag_needed(True)
        capture("blackhole", root / "blackhole", ("health", "upload"))
        mss_rule(True)
        capture("mitigated", root / "mitigated", ("health", "upload"))
        mss_rule(False)
        fixture.drop_frag_needed(False)
        fixture.mtulimit(1500)
        run_ns(SRC, "ip", "route", "flush", "cache")
        capture("recovery", root / "recovery", ("upload",))
        status = "PASS"
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"NOT_VERIFIED: {exc}", file=sys.stderr)
        (root / "failure.txt").write_text(str(exc) + "\n")
    finally:
        if server is not None and server.poll() is None:
            os.killpg(server.pid, signal.SIGTERM)
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(server.pid, signal.SIGKILL)
                server.wait(timeout=5)
        if logs is not None:
            logs.close()
        fixture.cleanup()
        found = {line.split()[0] for line in command("ip", "netns", "list", check=False).stdout.splitlines() if line.strip()}
        remain = sorted(set(NAMES).intersection(found))
        if remain:
            status = "FAILED_CLEANUP"
        (root / "result.json").write_text(json.dumps(
            {"result": status, "namespaces_remaining": remain}, indent=2) + "\n")
        checksum(root)
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
