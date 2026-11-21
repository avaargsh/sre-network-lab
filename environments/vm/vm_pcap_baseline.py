#!/usr/bin/env python3
"""Read-only multi-VM TCP baseline capture orchestrator for LAB-03D.

Starts bounded systemd-managed tcpdump on three VirtualBox guests (four NICs),
runs synthetic HTTP, retrieves PCAP over Vagrant SSH and independently verifies
the four-point TCP flow. Never injects MTU/firewall faults.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from http_vm import SHA256
from lab_vm import (
    ROOT, VM_DIR, guest_call, require_http_proof,
    require_independent_vms,
)
from pcap_vm import ROLE_IPS, ROLE_SIDES, MAX_PCAP_BYTES, validate_run_id
from verify_vm_pcap import CAPTURES, require_four_point_flow, decode_pcap, verify

PREFIX = "SRE_LAB_PCAP="
GUEST_HELPER = "/usr/local/libexec/sre-lab03d-pcap.py"
ROLES = ("src", "router", "dst")
STATUS = {
    "result": "NOT_VERIFIED", "scope": "3 independent VirtualBox VM NICs / 4 TCP captures",
    "pcap": "NOT_VERIFIED", "http": "NOT_VERIFIED",
    "mtu_fault": "NOT_VERIFIED", "tcp_blackhole": "NOT_VERIFIED",
}


def parse_guest_output(stdout: str) -> dict:
    records = [line.strip()[len(PREFIX):] for line in stdout.splitlines()
               if line.strip().startswith(PREFIX)]
    if len(records) != 1:
        raise ValueError(f"capture helper provided {len(records)} records, expected one")
    result = json.loads(records[0])
    if not isinstance(result, dict):
        raise ValueError("malformed capture helper JSON")
    return result


def guest_capture(role: str, action: str, run_id: str) -> dict:
    if role not in ROLES or action not in ("start", "finish"):
        raise ValueError("not an approved VM capture action")
    validate_run_id(run_id)
    remote = f"sudo -n /usr/bin/python3 {GUEST_HELPER} {action} {role} {run_id}"
    p = subprocess.run(["vagrant", "ssh", role, "-c", remote],
                       cwd=VM_DIR, capture_output=True, text=True, timeout=105)
    if p.returncode:
        raise RuntimeError(f"{role}/{action} returned {p.returncode}: {p.stderr[-500:]}")
    result = parse_guest_output(p.stdout)
    if result.get("run_id") != run_id or result.get("role") != role \
            or result.get("action") != action:
        raise ValueError("capture helper returned unrelated run/role")
    return result


def require_started(receipt: dict, role: str, topology: dict) -> None:
    if receipt.get("status") != "ARMED" or receipt.get("runtime_max_seconds") != 120 \
            or receipt.get("packet_cap") != 512:
        raise ValueError(f"{role}: expected bounded systemd capture")
    values = receipt.get("interfaces", {})
    if not isinstance(values, dict) or set(values) != set(ROLE_SIDES[role]):
        raise ValueError(f"{role}: unexpected capture interfaces")
    for side, dev in values.items():
        if not isinstance(dev, str) or topology[role]["links"].get(dev) != ROLE_IPS[role][side]:
            raise ValueError(f"{role}: capture is not on the dedicated private data NIC")


def decode_receipt(receipt: dict, role: str, topology: dict) -> tuple[dict, dict]:
    if receipt.get("status") != "STOPPED":
        raise ValueError(f"{role}: capture was not stopped")
    raw = receipt.get("captures")
    if not isinstance(raw, dict) or set(raw) != set(ROLE_SIDES[role]):
        raise ValueError(f"{role}: expected exact per-guest captures")
    binary, metadata = {}, {}
    for side in ROLE_SIDES[role]:
        item = raw[side]
        interface = item.get("interface", "")
        if topology[role]["links"].get(interface) != ROLE_IPS[role][side]:
            raise ValueError("exported capture has foreign interface")
        try:
            data = base64.b64decode(item["data"], validate=True)
        except (ValueError, TypeError, binascii.Error) as exc:
            raise ValueError("invalid base64 PCAP") from exc
        if not 40 < len(data) <= MAX_PCAP_BYTES:
            raise ValueError("PCAP size is outside fixed safety limit")
        sha = hashlib.sha256(data).hexdigest()
        if item.get("size") != len(data) or item.get("sha256") != sha:
            raise ValueError("guest capture checksum and length mismatch")
        key = role + ("-" + side if role == "router" else "")
        binary[key] = data
        metadata[key] = {"role": role, "private_ip": ROLE_IPS[role][side],
                         "interface": interface, "size": len(data), "sha256": sha}
    return binary, metadata


def store(folder: Path, records: dict, pcaps: dict) -> None:
    for key, value in records.items():
        (folder / (key + ".json")).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    for key, data in pcaps.items():
        (folder / (key + ".pcap")).write_bytes(data)
    hashes = [
        f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}"
        for p in sorted(folder.iterdir()) if p.is_file() and p.name != "SHA256SUMS"
    ]
    (folder / "SHA256SUMS").write_text("\n".join(hashes) + "\n")


def capture_baseline(run_id: str) -> int:
    validate_run_id(run_id)
    folder = ROOT / "evidence" / run_id
    folder.mkdir(mode=0o700, parents=True, exist_ok=False)
    records = {"status": dict(STATUS)}
    pcaps: dict[str, bytes] = {}
    started: list[str] = []
    error: str | None = None
    try:
        topology = {role: guest_call(role, "attest") for role in ROLES}
        require_independent_vms(topology)
        records["topology"] = topology
        # Verify the synthetic service before scheduling any capture.
        require_http_proof({op: guest_call("src", op)
                            for op in ("health", "upload")}, SHA256)
        starts = {}
        for role in ROLES:
            started.append(role)  # finish attempted even if start times out
            receipt = guest_capture(role, "start", run_id)
            require_started(receipt, role, topology)
            starts[role] = receipt
        records["http"] = {op: guest_call("src", op) for op in ("health", "upload")}
        require_http_proof(records["http"], SHA256)
    except (OSError, RuntimeError, ValueError, KeyError, TypeError,
            subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        error = str(exc)[-1500:]
    finally:
        for role in reversed(started):
            try:
                receipt = guest_capture(role, "finish", run_id)
                captures, meta = decode_receipt(receipt, role, records["topology"])
                pcaps.update(captures)
                records.setdefault("capture-metadata", {}).update(meta)
            except (OSError, RuntimeError, ValueError, KeyError, TypeError,
                    subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
                error = (error + "; " if error else "") + f"{role}/finish: {str(exc)[-550:]}"
        if error is None:
            try:
                parsed = {side: decode_pcap(pcaps[side]) for side in CAPTURES}
                require_four_point_flow(parsed)
                records["status"].update({"result": "PASS", "pcap": "PASS", "http": "PASS"})
            except (KeyError, ValueError, TypeError) as exc:
                error = "PCAP correlation: " + str(exc)
        if error:
            # Failure metadata is not a green evidence package and cannot be
            # misread by the independent verifier as a verified baseline.
            records["failure"] = {"reason": error}
        store(folder, records, pcaps)
    if error:
        print("NOT_VERIFIED: " + error, file=sys.stderr)
        return 1
    try:
        result = verify(folder, SHA256)
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        # A stored PASS that the independent auditor rejected is dangerous.
        # Rewrite it to NOT_VERIFIED, include failure and renew SHA-256 manifest.
        records["status"] = dict(STATUS)
        records["failure"] = {"reason": "independent audit rejected: " + str(exc)[-800:]}
        store(folder, records, pcaps)
        print("NOT_VERIFIED: independent evidence audit failed: " + str(exc), file=sys.stderr)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("baseline-pcap",))
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    try:
        return capture_baseline(args.run_id)
    except (OSError, ValueError) as exc:
        print("REFUSED: " + str(exc), file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
