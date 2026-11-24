#!/usr/bin/env python3
"""LAB-03D Stage 2A: scoped Router MTU lease and manual/watchdog recovery evidence.

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
from router_fault import (
    TTL, require_authorization, require_run_id, remote_argv,
)

PREFIX = "SRE_LAB_FAULT="
WATCHDOG_GRACE_SECONDS = 5
WATCHDOG_WAIT_SECONDS = TTL + WATCHDOG_GRACE_SECONDS
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
            or r.get("mtu") != 1200 or r.get("timer_active") is not True
            or not isinstance(r.get("deadline_epoch"), int)
            or not 10 < r["deadline_epoch"] - now <= TTL + 5):
        raise ValueError("Router fault lacks live watchdog or has expired")


def require_restored(r: dict, run_id: str) -> None:
    if (r.get("status") != "RESTORED" or r.get("run_id") != run_id
            or r.get("mtu") != 1500):
        raise ValueError("Router did not prove manual MTU recovery")


def require_watchdog_restored(r: dict, run_id: str) -> dict:
    """Require the expired transient timer to have removed its lease and restored MTU."""
    if (r.get("status") != "NO_ACTIVE_LEASE" or r.get("run_id") != run_id
            or r.get("mtu") != 1500 or r.get("timer_active") is not False
            or not isinstance(r.get("interface"), str) or not r["interface"]):
        raise ValueError(
            "Router watchdog did not prove lease expiry, inactive timer and MTU 1500"
        )
    return {
        "status": "RESTORED",
        "run_id": run_id,
        "mtu": 1500,
        "source": "systemd-watchdog",
    }


def wait_for_watchdog(sleep_fn=None, monotonic_fn=None) -> float:
    """Wait out the full lease plus grace without opening another SSH session."""
    sleep_fn = time.sleep if sleep_fn is None else sleep_fn
    monotonic_fn = time.monotonic if monotonic_fn is None else monotonic_fn
    started = monotonic_fn()
    sleep_fn(WATCHDOG_WAIT_SECONDS)
    elapsed = monotonic_fn() - started
    if elapsed < WATCHDOG_WAIT_SECONDS:
        raise ValueError("watchdog wait ended before the full lease plus grace")
    return round(elapsed, 3)


def remote_shell(op: str, run_id: str) -> str:
    argv = remote_argv(op, require_run_id(run_id))
    if op in ("arm", "arm-blackhole"):
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


def drill(run_id: str, rollback_mode: str = "manual") -> int:
    require_run_id(run_id)
    if rollback_mode not in ("manual", "watchdog"):
        raise ValueError("rollback mode must be manual or watchdog")
    require_authorization(os.getenv("SRE_LAB_VM_FAULT_ACK", ""),
                          os.getenv("SRE_LAB_VM_SCOPE", ""))
    destination = ROOT / "evidence" / run_id
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    evidence = {
        "status": {
            "result": "NOT_VERIFIED",
            "scope": "3 VirtualBox VMs; actual E2E required",
            "watchdog": "NOT_VERIFIED",
            **UNVERIFIED,
        },
        "watchdog": {"status": "NOT_RUN", "run_id": run_id},
    }
    result = 1
    attempted_arm = False
    watchdog_confirmed = False
    try:
        reports = {role: guest_call(role, "attest")
                   for role in ("src", "router", "dst")}
        require_independent_vms(reports)
        evidence["topology"] = reports
        baseline = {op: guest_call("src", op) for op in ("health", "upload")}
        require_http_proof(baseline, SHA256)
        evidence["baseline"] = baseline

        # Register cleanup ownership before the remote call: an SSH timeout may
        # occur after the guest has armed the timer and changed the interface.
        attempted_arm = True
        armed = router_call("arm", run_id)
        evidence["lease"] = armed
        require_armed(armed, run_id, time.time())
        checked = router_call("inspect", run_id)
        evidence["fault_inspect"] = checked
        require_armed(checked, run_id, time.time())
        if checked.get("interface") != armed.get("interface"):
            raise ValueError("Router interface changed during the fault lease")

        # MTU-only stage: ICMP Frag Needed is NOT dropped, so HTTP may
        # succeed through PMTUD. A timeout is recorded, never called PASS.
        evidence["fault_health"] = guest_call("src", "health")
        if evidence["fault_health"].get("ok") is not True:
            raise ValueError("small HTTP health failed under controlled MTU")
        try:
            evidence["fault_upload"] = guest_call("src", "upload")
        except (ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            evidence["fault_upload"] = {"ok": False, "error": str(exc)[-800:]}

        if rollback_mode == "watchdog":
            # The arm SSH process has already exited. During this wait the host
            # deliberately makes no Router SSH call; only systemd can recover.
            wait_started_epoch = int(time.time())
            waited = wait_for_watchdog()
            wait_finished_epoch = int(time.time())
            observed = router_call("inspect", run_id)
            evidence["watchdog"] = {
                **observed,
                "wait_started_epoch": wait_started_epoch,
                "wait_finished_epoch": wait_finished_epoch,
                "waited_seconds": waited,
            }
            if observed.get("interface") != armed.get("interface"):
                raise ValueError("watchdog inspected a different Router interface")
            evidence["rollback"] = require_watchdog_restored(observed, run_id)
            watchdog_confirmed = True
            evidence["status"]["watchdog"] = "PASS"
        else:
            evidence["watchdog"] = {"status": "NOT_RUN", "run_id": run_id}

    except (OSError, RuntimeError, ValueError, KeyError,
            subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        evidence["error"] = {"message": str(exc)[-1500:]}
    finally:
        # If watchdog proof was absent, use the manual path as a safety fallback.
        # A successful fallback does not turn a failed watchdog experiment green.
        if attempted_arm and not watchdog_confirmed:
            try:
                recovered = router_call("restore", run_id)
                evidence["rollback"] = recovered
                require_restored(recovered, run_id)
            except (OSError, RuntimeError, ValueError, KeyError,
                    subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
                evidence["recovery_error"] = {"message": str(exc)[-1500:]}

        # Only after a rollback receipt has been checked do we probe application
        # recovery. For watchdog mode this occurs after the post-expiry inspect.
        if "rollback" in evidence:
            try:
                after = {op: guest_call("src", op) for op in ("health", "upload")}
                require_http_proof(after, SHA256)
                evidence["recovery"] = after
                if "error" not in evidence and "recovery_error" not in evidence:
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
    parser.add_argument("--rollback-mode", choices=("manual", "watchdog"),
                        default="manual")
    args = parser.parse_args()
    try:
        ret = drill(args.run_id, rollback_mode=args.rollback_mode)
        mode = "90-second systemd watchdog" if args.rollback_mode == "watchdog" \
            else "bounded Router MTU lease + manual recovery"
        print(f"PASS ({mode})" if ret == 0
              else "NOT_VERIFIED: inspect evidence and Router timer",
              file=sys.stdout if ret == 0 else sys.stderr)
        return ret
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
