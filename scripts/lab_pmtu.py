#!/usr/bin/env python3
"""LAB-03B phase 1: Linux netns PMTU discovery, blackhole, and recovery.

Changes only three newly-created network namespaces. Does NOT configure real VM NICs.
"""
from __future__ import annotations

import hashlib
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

ROOT = Path(__file__).resolve().parents[1]
NAMES = ("sre-pmtu-src", "sre-pmtu-hop", "sre-pmtu-dst")
SRC, HOP, DST = NAMES
SRC_IP, HOP_LEFT, HOP_RIGHT, DST_IP = ("10.207.10.2", "10.207.10.1",
                                     "10.207.20.1", "10.207.20.2")
DROP_RULE = ("-p", "icmp", "--icmp-type", "fragmentation-needed", "-d", SRC_IP, "-j", "DROP")
LARGE_SIZE = 1400  # 1400 data + 8 ICMP + 20 IPv4 = 1428 > bottleneck 1200
SMALL_SIZE = 900    # IPv4 packet 928 <= bottleneck 1200


def require_approval(ack: str, scope: str, uid: int) -> None:
    if ack != "1" or scope != "dedicated-netns" or uid != 0:
        raise ValueError("requires root and explicit SRE_LAB_ALLOW_MUTATIONS=1 / SRE_LAB_SCOPE=dedicated-netns")


def has_echo(decoded: str, data_bytes: int) -> bool:
    """Match actual src→dst ICMP Echo Request and length in the same decoded line."""
    expected_icmp_length = data_bytes + 8
    regex = (r"\b" + re.escape(SRC_IP) + r"\s*>\s*" + re.escape(DST_IP)
             + r":\s*ICMP echo request,.*\blength\s+" + str(expected_icmp_length) + r"\b")
    return bool(re.search(regex, decoded))


def has_frag_needed(decoded: str) -> bool:
    regex = (r"\b" + re.escape(HOP_LEFT) + r"\s*>\s*" + re.escape(SRC_IP)
             + r":\s*ICMP.*(?:need to frag|frag needed|fragmentation needed).*\bmtu\s*1200\b")
    return bool(re.search(regex, decoded, re.IGNORECASE))


def check_phase(phase: str, pings: dict, ingress: str, egress: str) -> None:
    def passed(name: str) -> bool:
        return (name in pings and pings[name][0] == 0
                and "2 received" in pings[name][1])

    if phase in ("baseline", "recovery"):
        if not passed("large") or not has_echo(ingress, LARGE_SIZE) or not has_echo(egress, LARGE_SIZE):
            raise ValueError(f"{phase}: large DF packets must succeed across BOTH router interfaces")
    elif phase == "blackhole":
        if not passed("small"):
            raise ValueError("blackhole: small packets must still pass")
        if (not has_echo(ingress, LARGE_SIZE) or not has_echo(ingress, SMALL_SIZE)
                or not has_echo(egress, SMALL_SIZE) or has_echo(egress, LARGE_SIZE)):
            raise ValueError("blackhole: expected large ingress, only small egress")
        large = pings.get("large", (0, "")) 
        if (large[0] == 0 or "100% packet loss" not in large[1]
                or has_frag_needed(ingress) or "Frag needed" in large[1]):
            raise ValueError("blackhole: require timeout without ICMP Frag Needed")
    elif phase == "discovery":
        large = pings.get("large", (0, ""))
        if (large[0] == 0 or not has_echo(ingress, LARGE_SIZE)
                or has_echo(egress, LARGE_SIZE) or not has_frag_needed(ingress)):
            raise ValueError("discovery: router must return a REAL ICMP Frag Needed at MTU 1200")
    else:
        raise ValueError(f"unknown phase {phase}")


def command(*args: str, check: bool = True, timeout: int = 30) -> subprocess.CompletedProcess:
    proc = subprocess.run(list(args), text=True, capture_output=True, timeout=timeout)
    if check and proc.returncode:
        raise RuntimeError(
            f"command failed rc={proc.returncode}: {' '.join(args)}\n{proc.stderr[-1200:]}"
        )
    return proc


def run_ns(ns: str, *args: str, check: bool = True, timeout: int = 30) -> subprocess.CompletedProcess:
    return command("ip", "netns", "exec", ns, *args, check=check, timeout=timeout)


def record_phase(phase: str, output: Path, log: dict) -> None:
    data = {"phase": phase, "time": datetime.now(timezone.utc).isoformat(),
            "status": "PASS", "pings": log, "scope": "isolated Linux network namespace"}
    (output / "status.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    checksum(output)


def checksum(folder: Path) -> None:
    entries = []
    for path in sorted(folder.iterdir()):
        if path.is_file() and path.name != "SHA256SUMS":
            entries.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
    (folder / "SHA256SUMS").write_text("\n".join(entries) + "\n")


class NetnsFixture:
    def __init__(self, base: Path):
        self.base = base
        self.created: list[str] = []

    def preflight(self) -> None:
        for binary in ("ip", "iptables", "ping", "tcpdump", "sysctl"):
            if not shutil.which(binary):
                raise RuntimeError(f"missing dependency: {binary}")
        listed = command("ip", "netns", "list").stdout
        present = {line.split()[0] for line in listed.splitlines() if line.strip()}
        conflicts = present.intersection(NAMES)
        if conflicts:
            raise RuntimeError(f"refusing to touch existing namespace(s): {sorted(conflicts)}")

    def setup(self) -> None:
        self.preflight()
        for ns in NAMES:
            command("ip", "netns", "add", ns)
            self.created.append(ns)
        for a, b, left, right in (
            ("srepm-a", "srepm-h0", SRC, HOP),
            ("srepm-b", "srepm-h1", DST, HOP),
        ):
            command("ip", "link", "add", a, "type", "veth", "peer", "name", b)
            command("ip", "link", "set", a, "netns", left)
            command("ip", "link", "set", b, "netns", right)

        for ns in NAMES:
            command("ip", "-n", ns, "link", "set", "lo", "up")
        for ns, old, new, ip in [
            (SRC, "srepm-a", "eth0", SRC_IP + "/24"),
            (HOP, "srepm-h0", "h0", HOP_LEFT + "/24"),
            (HOP, "srepm-h1", "h1", HOP_RIGHT + "/24"),
            (DST, "srepm-b", "eth0", DST_IP + "/24"),
        ]:
            command("ip", "-n", ns, "link", "set", "dev", old, "name", new)
            command("ip", "-n", ns, "address", "add", ip, "dev", new)
            command("ip", "-n", ns, "link", "set", "dev", new, "mtu", "1500")
            command("ip", "-n", ns, "link", "set", "dev", new, "up")

        command("ip", "-n", SRC, "route", "add", "default", "via", HOP_LEFT)
        command("ip", "-n", DST, "route", "add", "default", "via", HOP_RIGHT)
        run_ns(HOP, "sysctl", "-w", "net.ipv4.ip_forward=1")

    def mtulimit(self, size: int) -> None:
        if size not in (1200, 1500):
            raise ValueError("only fixed, tested MTUs 1200 or 1500 are permitted")
        command("ip", "-n", HOP, "link", "set", "dev", "h1", "mtu", str(size))

    def drop_frag_needed(self, enabled: bool) -> None:
        if enabled:
            run_ns(HOP, "iptables", "-I", "OUTPUT", "1", *DROP_RULE)
        else:
            result = run_ns(HOP, "iptables", "-C", "OUTPUT", *DROP_RULE, check=False)
            if result.returncode == 0:
                run_ns(HOP, "iptables", "-D", "OUTPUT", *DROP_RULE)

    def probe(self, phase: str, kind: str, size: int, output: Path) -> tuple[int, str]:
        proc = run_ns(SRC, "ping", "-n", "-M", "probe", "-c", "2", "-W", "1",
                      "-s", str(size), DST_IP, check=False, timeout=15)
        result = proc.stdout + proc.stderr
        (output / f"{kind}.ping.txt").write_text(
            f"command: ping -M probe -c 2 -W 1 -s {size} {DST_IP}\n"
            f"exit_code: {proc.returncode}\n" + result
        )
        return proc.returncode, result

    def evidence(self, phase: str, pings: tuple[str, ...]) -> None:
        folder = self.base / phase
        folder.mkdir(parents=True, exist_ok=False)
        recordings: list[tuple[subprocess.Popen, object]] = []
        try:
            for name, iface in (("ingress", "h0"), ("egress", "h1")):
                target = folder / f"{name}.pcap"
                fh = target.open("wb")
                proc = subprocess.Popen(
                    ["ip", "netns", "exec", HOP, "tcpdump", "-U", "-i", iface,
                     "-s", "0", "-n", "-w", "-", "icmp"],
                    stdout=fh, stderr=subprocess.PIPE, start_new_session=True
                )
                recordings.append((proc, fh))
            time.sleep(0.8)
            if any(p.poll() is not None for p, _ in recordings):
                raise RuntimeError("tcpdump exited before probes; check CAP_NET_RAW")
            results = {}
            for p in pings:
                results[p] = self.probe(phase, p, LARGE_SIZE if p == "large" else SMALL_SIZE, folder)
        finally:
            for proc, fh in recordings:
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGINT)
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait(timeout=3)
                fh.close()
                stderr = proc.stderr.read() if proc.stderr is not None else b""
                # tcpdump writes diagnostics but no sensitive secrets in this isolated fixture.
                (folder / (("ingress" if "h0" in proc.args else "egress") + ".tcpdump.stderr")).write_bytes(stderr)
                if proc.stderr:
                    proc.stderr.close()
        decoded = {}
        for name in ("ingress", "egress"):
            file = folder / f"{name}.pcap"
            if file.stat().st_size < 40:
                raise RuntimeError(f"empty {name} pcap")
            data = command("tcpdump", "-tt", "-nn", "-vv", "-r", str(file)).stdout
            (folder / f"{name}.decoded.txt").write_text(data)
            decoded[name] = data
        check_phase(phase, results, decoded["ingress"], decoded["egress"])
        record_phase(phase, folder, {
            key: {"exit_code": rc, "output": output} for key, (rc, output) in results.items()
        })
        print(f"PASS {phase} - ICMP probes and both router-interface captures agree")

    def cleanup(self) -> None:
        for ns in reversed(self.created):
            command("ip", "netns", "del", ns, check=False)


def main() -> int:
    try:
        require_approval(os.getenv("SRE_LAB_ALLOW_MUTATIONS", ""),
                         os.getenv("SRE_LAB_SCOPE", ""), os.geteuid())
        run_id = os.getenv("RUN_ID", "")
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,75}", run_id):
            raise ValueError("safe explicit RUN_ID is required")
        root = Path(os.getenv("EVIDENCE_DIR", str(ROOT / "evidence"))) / run_id
        # Never overwrite existing evidence or mix multiple attempts.
        root.mkdir(parents=True, mode=0o700, exist_ok=False)
        fixture = NetnsFixture(root)
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"REFUSED {exc}", file=sys.stderr)
        return 3

    status = "FAILED"
    try:
        fixture.setup()
        (root / "environment.json").write_text(json.dumps({
            "run_id": run_id, "kernel": command("uname", "-r").stdout.strip(),
            "topology": {"src": SRC_IP, "router_in": HOP_LEFT,
                         "router_out": HOP_RIGHT, "dst": DST_IP},
            "scope": "one isolated VM/runner with three network namespaces; not two real VMs",
            "time": datetime.now(timezone.utc).isoformat()
        }, indent=2) + "\n")

        fixture.evidence("baseline", ("large",))
        fixture.mtulimit(1200)
        fixture.drop_frag_needed(True)
        fixture.evidence("blackhole", ("small", "large"))
        fixture.drop_frag_needed(False)
        fixture.evidence("discovery", ("large",))
        fixture.mtulimit(1500)
        fixture.evidence("recovery", ("large",))
        status = "PASS"
        print("LAB-03B fixture PASS (not independent-VM E2E)")
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"LAB-03B NOT VERIFIED: {exc}", file=sys.stderr)
        (root / "failure.txt").write_text(str(exc) + "\n")
    finally:
        fixture.cleanup()
        remaining = {row.split()[0] for row in command("ip", "netns", "list", check=False).stdout.splitlines()
                     if row.strip()}
        if any(name in remaining for name in NAMES):
            status = "FAILED_CLEANUP"
        (root / "result.json").write_text(json.dumps({"result": status,
            "namespaces_remaining": sorted(set(NAMES).intersection(remaining)),
            "time": datetime.now(timezone.utc).isoformat()}, indent=2) + "\n")
        checksum(root)
    # Never return GREEN if teardown left any reserved namespace behind.
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
