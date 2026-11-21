#!/usr/bin/env python3
"""Read-only 3-VM identity, route and synthetic HTTP acceptance gate."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
VM_DIR = Path(__file__).resolve().parent
LAB = "sre-lab03d-vbox-intnet"
ROLES = ("src", "router", "dst")
EXPECTED = {
    "src": {"10.209.10.2"},
    "router": {"10.209.10.1", "10.209.20.1"},
    "dst": {"10.209.20.2"},
}
ATTEST = "SRE_LAB_ATTEST="


def require_run_id(value):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,75}", value):
        raise ValueError("unsafe RUN_ID")
    return value


def parse_attestation(output):
    lines = [line.strip()[len(ATTEST):] for line in output.splitlines()
             if line.strip().startswith(ATTEST)]
    if len(lines) != 1:
        raise ValueError("expected exactly one in-guest signed-scope JSON line")
    record = json.loads(lines[0])
    if not isinstance(record, dict):
        raise ValueError("attestation is not JSON object")
    return record


def require_independent_vms(reports):
    if set(reports) != set(ROLES):
        raise ValueError("need src/router/dst attestation")
    machines, boots = set(), set()
    for role in ROLES:
        r = reports[role]
        if r.get("lab") != LAB or r.get("role") != role:
            raise ValueError(f"{role}: wrong marker or VM role")
        machine, boot = r.get("machine_id", ""), r.get("boot_id")
        if not re.fullmatch(r"[0-9a-f]{32}", machine):
            raise ValueError(f"{role}: invalid machine-id")
        try:
            uuid.UUID(boot)
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError(f"{role}: invalid boot-id") from exc
        machines.add(machine)
        boots.add(boot)
        links = r.get("links")
        if not isinstance(links, dict) or set(links.values()) != EXPECTED[role] \
                or len(links) != len(EXPECTED[role]):
            raise ValueError(f"{role}: unexpected isolated NIC addresses")
        if any(not re.fullmatch(r"[a-zA-Z0-9_.-]+", dev) for dev in links):
            raise ValueError(f"{role}: invalid private interface")
        if not r.get("default_iface") or r["default_iface"] in links:
            raise ValueError(f"{role}: private data NIC overlaps management default route")
    if len(machines) != 3 or len(boots) != 3:
        raise ValueError("machine-id/boot-id are not unique; not independent VM proof")
    if reports["router"].get("ip_forward") != "1":
        raise ValueError("router does not forward IPv4")
    for role, gateway, target, local in (
        ("src", "10.209.10.1", "10.209.20.2", "10.209.10.2"),
        ("dst", "10.209.20.1", "10.209.10.2", "10.209.20.2"),
    ):
        r = reports[role]
        dev = next(k for k, v in r["links"].items() if v == local)
        route = r.get("route") or {}
        if route.get("gateway") != gateway or route.get("destination") != target \
                or route.get("src") != local or route.get("dev") != dev:
            raise ValueError(f"{role}: cross-subnet route bypasses isolated router")


def require_http_proof(results, sha):
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise ValueError("invalid expected digest")
    h, u = results.get("health", {}), results.get("upload", {})
    if h.get("ok") is not True or h.get("status") != 200 or h.get("body") != "healthy":
        raise ValueError("real health response did not match")
    if u.get("ok") is not True or u.get("status") != 200 or u.get("digest") != sha:
        raise ValueError("real 64KiB POST response/digest did not match")


def guest_call(role, action):
    if role not in ROLES or action not in ("attest", "health", "upload"):
        raise ValueError("unsupported role/action")
    # Fixed tokens only: cannot pass an arbitrary remote command.
    remote = f"python3 /vagrant/environments/vm/guest.py {action} {role}"
    result = subprocess.run(["vagrant", "ssh", role, "-c", remote],
                            cwd=VM_DIR, text=True, capture_output=True, timeout=90)
    if result.returncode:
        raise RuntimeError(f"{role}/{action}: VM command failed: {result.stderr[-900:]}")
    return parse_attestation(result.stdout)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("attest", "baseline"))
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    try:
        run_id = require_run_id(args.run_id)
        reports = {role: guest_call(role, "attest") for role in ROLES}
        require_independent_vms(reports)
        result = {"topology": "PASS", "http": "NOT_VERIFIED"}
        if args.action == "baseline":
            from http_vm import SHA256
            observations = {kind: guest_call("src", kind) for kind in ("health", "upload")}
            require_http_proof(observations, SHA256)
            result["http"] = "PASS"
        else:
            observations = {}
        path = ROOT / "evidence" / run_id
        path.mkdir(parents=True, mode=0o700, exist_ok=False)
        (path / "vm-attestations.json").write_text(json.dumps(reports, indent=2) + "\n")
        (path / "http.json").write_text(json.dumps(observations, indent=2) + "\n")
        (path / "status.json").write_text(json.dumps({
            "lab": LAB, "status": "PASS", "scope": "3 independent VirtualBox guests",
            **result, "mtu_injection": "NOT_VERIFIED", "pcap": "NOT_VERIFIED",
        }, indent=2) + "\n")
        files = sorted(p for p in path.iterdir() if p.is_file())
        (path / "SHA256SUMS").write_text(
            "".join(f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.name}\n"
                    for f in files))
        print(f"PASS {args.action}: distinct VMs and scoped routing; evidence={path}")
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired,
            json.JSONDecodeError) as exc:
        print(f"NOT_VERIFIED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
