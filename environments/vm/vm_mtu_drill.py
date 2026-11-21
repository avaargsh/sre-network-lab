#!/usr/bin/env python3
"""LAB-03D Stage 2A: preflight, scoped Router MTU lease and recovery evidence.

Only operates on the Vagrant-managed three VirtualBox lab guests. No physical
host routing, management NIC or firewall mutation is permitted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from http_vm import SHA256
from lab_vm import (
    LAB, ROOT, VM_DIR, guest_call, require_http_proof,
    require_independent_vms,
)
from router_fault import require_authorization, require_run_id, remote_argv

PREFIX = "SRE_LAB_FAULT="
UNVERIFIED = {
    "network": "NOT_VERIFIED", "pcap": "NOT_VERIFIED",
    "blackhole": "NOT_VERIFIED",
}


def parse_report(output: str) -> dict:
    lines = [line.strip()[len(PREFIX):] for line in output.splitlines()
             if line.strip().startswith(PREFIX)]
    if len(lines) != 1:
        raise ValueError("expected exactly one Router fault acknowledgement")
    parsed = json.loads(lines[0])
    if not isinstance(parsed, dict):
        raise ValueError("invalid Router JSON report")
    return parsed


def require_armed(r: dict, run_id: str, now: float) -> None:
    if (r.get("run_id") != run_id or r.get("status") != "ARMED"
            or r.get("mtu") != 1200 or not r.get("timer_active") is True
            or not isinstance(r.get("deadline_epoch"), int)
            or not 10 < r["deadline_epoch"] - now <= 95):
        raise ValueError("Router fault lacks live watchdog or has expired")


def require_restored(r: dict, run_id: str) -> None:
    if (r.get("status") != "RESTORED" or r.get("run_id") != run_id
            or r.get("mtu") != 1500):
        raise ValueError("Router did not prove manual MTU recovery")


def remote_shell(op: str, run_id: str) -> str:
    argv = remote_argv(op, require_run_id(run_id))
    if op == "arm":
        prefix = ("sudo -n env SRE_LAB_VM_FAULT_ACK=1 "
                  f"SRE_LAB_VM_SCOPE={LAB} ")
    else:
        prefix = "sudo -n "
    return prefix + " ".join(argv)


def router_call(op: str, run_id: str) -> dict:
    p = subprocess.run(["vagrant", "ssh", "router", "-c", remote_shell(op, run_id)],
                       cwd=VM_DIR, capture_output=True, text=True, timeout=45)
    if p.returncode:
        raise RuntimeError(f"router {op} failed: {p.stderr[-1000:]}")
    return parse_report(p.stdout)


def write_evidence(folder: Path, records: dict) -> None:
    for name, content in sorted(records.items()):
        (folder / (name + ".json")).write_text(
            json.dumps(content, indent=2, sort_keys=True) + "\n"
        )
    hashes = [
        f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}"
        for p in sorted(folder.iterdir()) if p.is_file() and p.name != "SHA256SUMS"
    ]
    (folder / "SHA256SUMS").write_text("\n".join(hashes) + "\n")


def drill(run_id: str) -> int:
    require_run_id(run_id)
    require_authorization(os.getenv("SRE_LAB_VM_FAULT_ACK", ""),
                          os.getenv("SRE_LAB_VM_SCOPE", ""))
    destination = ROOT / "evidence" / run_id
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    evidence = {"status": {"result": "NOT_VERIFIED",
                           "scope": "3 VirtualBox VMs; actual E2E required",
                           **UNVERIFIED}}
    result = 1
    attempted_arm = False
    try:
        reports = {role: guest_call(role, "attest")
                   for role in ("src", "router", "dst")}
        require_independent_vms(reports)
        evidence["topology"] = reports
        baseline = {op: guest_call("src", op) for op in ("health", "upload")}
        require_http_proof(baseline, SHA256)
        evidence["baseline"] = baseline

        attempted_arm = True
        armed = router_call("arm", run_id)
        evidence["lease"] = armed
        require_armed(armed, run_id, time.time())
        checked = router_call("inspect", run_id)
        evidence["fault_inspect"] = checked
        require_armed(checked, run_id, time.time())
        # MTU-only stage: ICMP Frag Needed is NOT dropped, so HTTP may
        # succeed through PMTUD. A timeout is recorded, never called PASS.
        evidence["fault_health"] = guest_call("src", "health")
        if not (evidence["fault_health"].get("ok") is True):
            raise ValueError("small HTTP health failed under controlled MTU")
        try:
            evidence["fault_upload"] = guest_call("src", "upload")
        except (ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            evidence["fault_upload"] = {"ok": False, "error": str(exc)[-800:]}
    except (OSError, RuntimeError, ValueError, KeyError,
            subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        evidence["error"] = {"message": str(exc)[-1500:]}
    finally:
        if attempted_arm:
            try:
                recovered = router_call("restore", run_id)
                evidence["rollback"] = recovered
                require_restored(recovered, run_id)
                after = {op: guest_call("src", op) for op in ("health", "upload")}
                require_http_proof(after, SHA256)
                evidence["recovery"] = after
                if "error" not in evidence:
                    evidence["status"].update({
                        "result": "PASS", "topology": "PASS",
                        "baseline_http": "PASS", "mtu_lease": "PASS",
                        "recovery_http": "PASS",
                    })
                    result = 0
            except (OSError, RuntimeError, ValueError, KeyError,
                    subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
                evidence["recovery_error"] = {"message": str(exc)[-1500:]}
        write_evidence(destination, evidence)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("drill",))
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    try:
        ret = drill(args.run_id)
        print("PASS (bounded Router MTU lease + recovery)" if ret == 0
              else "NOT_VERIFIED: inspect evidence and Router timer", file=sys.stdout if ret == 0 else sys.stderr)
        return ret
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
