#!/usr/bin/env python3
"""LAB-03D guest: fixed-interface, bounded, synthetic TCP packet capture.

Only runs as root on a provisioned VirtualBox src/router/dst guest. It starts
systemd transient tcpdump services (RuntimeMaxSec=120s, <=512 packets each)
or stops/exports their output. Never changes VM interfaces/firewall/routes.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

LAB = "sre-lab03d-vbox-intnet"
MARKER = Path("/etc/sre-network-lab/role.json")
STATE_DIR = Path("/run/sre-lab03d-capture")
SRC, DST, PORT = "10.209.10.2", "10.209.20.2", "18080"
PREFIX = "SRE_LAB_PCAP="
MAX_PCAP_BYTES = 256 * 1024
ROLE_SIDES = {"src": ("src",), "router": ("left", "right"), "dst": ("dst",)}
ROLE_IPS = {
    "src": {"src": SRC},
    "router": {"left": "10.209.10.1", "right": "10.209.20.1"},
    "dst": {"dst": DST},
}


def validate_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,47}", run_id):
        raise ValueError("unsafe run identifier")
    return run_id


def sides(role: str) -> tuple[str, ...]:
    if role not in ROLE_SIDES:
        raise ValueError("not a lab guest role")
    return ROLE_SIDES[role]


def choose_interfaces(role: str, report: dict) -> dict[str, str]:
    if report.get("role") != role:
        raise ValueError("guest role mismatch")
    links = report.get("links")
    wanted = ROLE_IPS[role]
    if (not isinstance(links, dict) or len(links) != len(wanted)
            or set(links.values()) != set(wanted.values())
            or not report.get("default_iface")
            or report["default_iface"] in links):
        raise ValueError("private interface identity mismatch / overlaps NAT management")
    mapping = {}
    for side, ip in wanted.items():
        candidates = [name for name, value in links.items() if value == ip]
        if len(candidates) != 1 or not re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", candidates[0]):
            raise ValueError("ambiguous or invalid capture interface")
        mapping[side] = candidates[0]
    return mapping


def unit_name(run_id: str, side: str) -> str:
    validate_run_id(run_id)
    if side not in ("src", "left", "right", "dst"):
        raise ValueError("not a known data interface")
    return f"sre-lab03d-cap-{run_id}-{side}"


def capture_path(run_id: str, side: str) -> Path:
    return STATE_DIR / f"{unit_name(run_id, side)}.pcap"


def capture_command(run_id: str, side: str, iface: str) -> tuple[str, ...]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", iface):
        raise ValueError("unsafe capture device")
    name = unit_name(run_id, side)
    return (
        "systemd-run", "--unit=" + name, "--service-type=exec",
        "--property=RuntimeMaxSec=120s", "--property=NoNewPrivileges=yes",
        "/usr/bin/tcpdump", "-Z", "root", "-i", iface, "-n", "-U", "-s", "160",
        "-c", "512", "-w", str(capture_path(run_id, side)),
        "tcp", "and", "host", SRC, "and", "host", DST,
        "and", "port", PORT
    )


def check_pcap_bytes(data: bytes) -> bool:
    if len(data) <= 40 or len(data) > MAX_PCAP_BYTES:
        raise ValueError("empty, truncated or over-limit capture")
    if data[:4] not in (b"\xd4\xc3\xb2\xa1", b"\xa1\xb2\xc3\xd4"):
        raise ValueError("not a supported classic pcap")
    return True


def cmd(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    p = subprocess.run(args, capture_output=True, text=True, timeout=25)
    if check and p.returncode:
        raise RuntimeError(f"{args[0]} returned {p.returncode}: {p.stderr[-500:]}")
    return p


def guest_report(role: str) -> dict:
    if not MARKER.is_file() or MARKER.is_symlink():
        raise ValueError("not a provisioned laboratory VM")
    marker = json.loads(MARKER.read_text())
    if marker != {"lab": LAB, "role": role, "network": "virtualbox-internal"}:
        raise ValueError("Vagrant guest marker mismatch")
    virt = cmd("systemd-detect-virt", "--vm").stdout.strip()
    if virt != "oracle":
        raise ValueError("requires independently booted VirtualBox guest")
    defaults = json.loads(cmd("ip", "-j", "-4", "route", "show", "default").stdout)
    if len(defaults) != 1:
        raise ValueError("management route is ambiguous")
    links = {}
    for card in json.loads(cmd("ip", "-j", "-4", "addr", "show").stdout):
        for addr in card.get("addr_info", []):
            ipv4 = addr.get("local", "")
            if isinstance(ipv4, str) and ipv4.startswith("10.209."):
                if card["ifname"] in links:
                    raise ValueError("multiple addresses per experiment NIC")
                links[card["ifname"]] = ipv4
    report = {"role": role, "links": links, "default_iface": defaults[0]["dev"]}
    choose_interfaces(role, report)
    return report


def ensure_state_dir() -> None:
    if STATE_DIR.is_symlink():
        raise ValueError("unsafe capture directory")
    STATE_DIR.mkdir(mode=0o700, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)


def active(name: str) -> bool:
    return cmd("systemctl", "is-active", "--quiet", name + ".service",
               check=False).returncode == 0


def start(role: str, run_id: str, interfaces: dict[str, str]) -> dict:
    started = []
    try:
        for side, dev in interfaces.items():
            if capture_path(run_id, side).exists():
                raise ValueError("capture path already exists, cannot overwrite")
            name = unit_name(run_id, side)
            if active(name):
                raise ValueError("capture service already active")
            cmd(*capture_command(run_id, side, dev))
            started.append(side)
            for _ in range(10):
                if active(name):
                    break
                time.sleep(0.15)
            if not active(name):
                raise RuntimeError("bounded tcpdump service failed to start")
        time.sleep(0.6)
        return {"action": "start", "role": role, "run_id": run_id,
                "status": "ARMED", "interfaces": interfaces,
                "runtime_max_seconds": 120, "packet_cap": 512}
    except Exception:
        for side in started:
            cmd("systemctl", "stop", unit_name(run_id, side) + ".service", check=False)
        raise


def finish(role: str, run_id: str, interfaces: dict[str, str]) -> dict:
    captures = {}
    errors = []
    for side, dev in interfaces.items():
        unit = unit_name(run_id, side) + ".service"
        # Graceful tcpdump SIGINT flushes the pcap header/record stream.
        cmd("systemctl", "kill", "--kill-who=main", "--signal=SIGINT",
            unit, check=False)
        cmd("systemctl", "stop", unit, check=False)
        file = capture_path(run_id, side)
        try:
            if not file.is_file() or file.is_symlink():
                raise ValueError("missing or unsafe tcpdump file")
            blob = file.read_bytes()
            check_pcap_bytes(blob)
            captures[side] = {
                "interface": dev, "sha256": hashlib.sha256(blob).hexdigest(),
                "size": len(blob), "data": base64.b64encode(blob).decode("ascii"),
            }
            file.unlink()
        except (ValueError, OSError) as exc:
            errors.append(f"{side}: {exc}")
    if errors:
        raise ValueError("capture rejected: " + "; ".join(errors))
    return {"action": "finish", "role": role, "run_id": run_id,
            "captures": captures, "status": "STOPPED"}


def main() -> int:
    if len(sys.argv) != 4 or sys.argv[1] not in ("start", "finish"):
        print("usage: pcap_vm.py {start|finish} <role> <run-id>", file=sys.stderr)
        return 2
    action, role, run_id = sys.argv[1:]
    try:
        validate_run_id(run_id)
        sides(role)
        if os.geteuid() != 0:
            raise ValueError("must run with root inside the isolated guest")
        report = guest_report(role)
        ensure_state_dir()
        interfaces = choose_interfaces(role, report)
        data = (start(role, run_id, interfaces) if action == "start"
                else finish(role, run_id, interfaces))
        print(PREFIX + json.dumps(data, separators=(",", ":"), sort_keys=True))
        return 0
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired,
            json.JSONDecodeError) as exc:
        print("NOT_VERIFIED: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
