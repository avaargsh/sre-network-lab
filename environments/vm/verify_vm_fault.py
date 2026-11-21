#!/usr/bin/env python3
"""Offline replay of LAB-03D MTU lease receipts; never claims uncollected PCAP.

SHA-256 is evidence file consistency, not attestation of external VM provenance.
"""
import hashlib
import json
from pathlib import Path
import re
import sys

from http_vm import SHA256
from lab_vm import require_independent_vms, require_http_proof
from vm_mtu_drill import require_armed, require_restored, UNVERIFIED

FILES = {"topology.json", "baseline.json", "fault_health.json",
         "fault_upload.json", "lease.json", "fault_inspect.json",
         "rollback.json", "recovery.json", "status.json"}


def verify(root: Path, expected_sha: str = SHA256):
    if not root.is_dir() or root.is_symlink():
        raise ValueError("unsafe/missing VM evidence directory")
    files = list(root.iterdir())
    if (any(not f.is_file() or f.is_symlink() for f in files)
            or {f.name for f in files} != FILES | {"SHA256SUMS"}):
        raise ValueError("missing or unaccounted evidence file")
    checksums = {}
    for line in (root / "SHA256SUMS").read_text().splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9_.-]+)", line)
        if not match or match[2] in checksums:
            raise ValueError("invalid/duplicate SHA-256 manifest line")
        checksums[match[2]] = match[1]
    if set(checksums) != FILES:
        raise ValueError("checksum manifest does not cover all evidence")
    for name, checksum in checksums.items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != checksum:
            raise ValueError(f"SHA-256 mismatch: {name}")
    read = lambda key: json.loads((root / (key + ".json")).read_text())
    status = read("status")
    expected = {"result": "PASS", "topology": "PASS", "mtu_lease": "PASS",
                "baseline_http": "PASS", "recovery_http": "PASS",
                "scope": "3 VirtualBox VMs; actual E2E required", **UNVERIFIED}
    if status != expected:
        raise ValueError("illegal success or false multi-VM PCAP/blackhole claim")
    topology = read("topology")
    require_independent_vms(topology)
    require_http_proof(read("baseline"), expected_sha)
    require_http_proof(read("recovery"), expected_sha)
    health = read("fault_health")
    if (health.get("ok") is not True or health.get("status") != 200
            or health.get("body") != "healthy"):
        raise ValueError("fault window small HTTP proof missing")
    run_id = root.name
    lease = read("lease")
    receipt = read("fault_inspect")
    if not isinstance(lease.get("deadline_epoch"), int):
        raise ValueError("watchdog deadline missing")
    approximate_start = lease["deadline_epoch"] - 90
    require_armed(lease, run_id, approximate_start)
    require_armed(receipt, run_id, approximate_start)
    if lease.get("interface") != receipt.get("interface") or \
            topology["router"]["links"].get(lease.get("interface")) != "10.209.20.1":
        raise ValueError("MTU lease target is not the Router private NIC")
    require_restored(read("rollback"), run_id)
    return {"status": "PASS", "files_hashed": len(checksums),
            "mtu_lease": "PASS", "recovery_http": "PASS", **UNVERIFIED}


if __name__ == "__main__":
    try:
        if len(sys.argv) != 2:
            raise ValueError("usage: verify_vm_fault.py <evidence-dir>")
        print(json.dumps(verify(Path(sys.argv[1])), indent=2))
    except (OSError, RuntimeError, ValueError, KeyError, TypeError,
            json.JSONDecodeError) as exc:
        print("NOT_VERIFIED: " + str(exc), file=sys.stderr)
        sys.exit(1)
