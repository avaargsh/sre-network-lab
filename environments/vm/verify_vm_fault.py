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
from vm_mtu_drill import (
    TTL, WATCHDOG_WAIT_SECONDS, require_armed, require_restored,
    require_watchdog_restored, UNVERIFIED,
)

FILES = {
    "topology.json", "baseline.json", "fault_health.json",
    "fault_upload.json", "lease.json", "fault_inspect.json",
    "watchdog.json", "rollback.json", "recovery.json", "status.json",
}


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
    run_id = root.name
    topology = read("topology")
    require_independent_vms(topology)
    require_http_proof(read("baseline"), expected_sha)
    require_http_proof(read("recovery"), expected_sha)

    lease = read("lease")
    receipt = read("fault_inspect")
    if not isinstance(lease.get("deadline_epoch"), int):
        raise ValueError("watchdog deadline missing")
    approximate_start = lease["deadline_epoch"] - TTL
    require_armed(lease, run_id, approximate_start)
    require_armed(receipt, run_id, approximate_start)
    if (lease.get("interface") != receipt.get("interface")
            or topology["router"]["links"].get(lease.get("interface")) != "10.209.20.1"):
        raise ValueError("MTU lease target is not the Router private NIC")

    watchdog = read("watchdog")
    if watchdog == {"status": "NOT_RUN", "run_id": run_id}:
        watchdog_status = "NOT_VERIFIED"
    else:
        require_watchdog_restored(watchdog, run_id)
        if watchdog.get("interface") != lease.get("interface"):
            raise ValueError("watchdog inspected a different Router interface")
        waited = watchdog.get("waited_seconds")
        if type(waited) not in (int, float) or waited < WATCHDOG_WAIT_SECONDS:
            raise ValueError("watchdog wait did not cover the full lease plus grace")
        started = watchdog.get("wait_started_epoch")
        finished = watchdog.get("wait_finished_epoch")
        if (type(started) is not int or type(finished) is not int
                or finished - started < TTL):
            raise ValueError("watchdog wall-clock interval is shorter than the lease")
        watchdog_status = "PASS"

    status = read("status")
    expected = {
        "result": "PASS", "topology": "PASS", "mtu_lease": "PASS",
        "baseline_http": "PASS", "recovery_http": "PASS",
        "watchdog": watchdog_status,
        "scope": "3 VirtualBox VMs; actual E2E required",
        **UNVERIFIED,
    }
    if status != expected:
        raise ValueError("illegal success or false watchdog/PCAP/blackhole claim")

    health = read("fault_health")
    if (health.get("ok") is not True or health.get("status") != 200
            or health.get("body") != "healthy"):
        raise ValueError("fault window small HTTP proof missing")
    rollback = read("rollback")
    require_restored(rollback, run_id)
    if watchdog_status == "PASS" and rollback.get("source") != "systemd-watchdog":
        raise ValueError("watchdog success lacks a timer-sourced rollback receipt")
    return {
        "status": "PASS", "files_hashed": len(checksums),
        "mtu_lease": "PASS", "recovery_http": "PASS",
        "watchdog": watchdog_status, **UNVERIFIED,
    }


if __name__ == "__main__":
    try:
        if len(sys.argv) != 2:
            raise ValueError("usage: verify_vm_fault.py <evidence-dir>")
        print(json.dumps(verify(Path(sys.argv[1])), indent=2))
    except (OSError, RuntimeError, ValueError, KeyError, TypeError,
            json.JSONDecodeError) as exc:
        print("NOT_VERIFIED: " + str(exc), file=sys.stderr)
        sys.exit(1)
