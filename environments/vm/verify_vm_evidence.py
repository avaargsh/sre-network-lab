#!/usr/bin/env python3
"""Independent offline replay of LAB-03D VM attestation and HTTP evidence.

This verifies consistency and SHA-256 manifests, NOT cryptographic provenance
or a real multi-VM packet capture.
"""
import hashlib
import json
from pathlib import Path
import re
import sys

from http_vm import SHA256
from lab_vm import LAB, require_http_proof, require_independent_vms

FILES = {"vm-attestations.json", "http.json", "status.json"}


def verify(root: Path, expected_sha: str) -> dict:
    if not root.is_dir() or root.is_symlink():
        raise ValueError("evidence root is missing or symbolic link")
    entries = list(root.iterdir())
    if any(e.is_symlink() or not e.is_file() for e in entries):
        raise ValueError("evidence must contain only regular files")
    actual = {e.name for e in entries}
    if actual != FILES | {"SHA256SUMS"}:
        raise ValueError("missing or extra evidence files")
    checksums = {}
    for row in (root / "SHA256SUMS").read_text().splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9_.-]+)", row)
        if not match or match[2] in checksums or match[2] == "SHA256SUMS":
            raise ValueError("invalid or duplicate checksum entry")
        checksums[match[2]] = match[1]
    if set(checksums) != FILES:
        raise ValueError("checksum manifest omits or adds files")
    for name, digest in checksums.items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"{name}: SHA-256 mismatch")
    status = json.loads((root / "status.json").read_text())
    if status.get("lab") != LAB or status.get("scope") != "3 independent VirtualBox guests" \
            or status.get("status") != "PASS" or status.get("topology") != "PASS":
        raise ValueError("incorrect lab identity or topology status")
    if status.get("pcap") != "NOT_VERIFIED" or status.get("mtu_injection") != "NOT_VERIFIED":
        raise ValueError("multi-VM packet capture or fault injection cannot be claimed yet")
    reports = json.loads((root / "vm-attestations.json").read_text())
    require_independent_vms(reports)
    http = json.loads((root / "http.json").read_text())
    http_status = status.get("http")
    if http_status == "PASS":
        require_http_proof(http, expected_sha)
    elif http_status == "NOT_VERIFIED":
        if http != {}:
            raise ValueError("unexpected unverified HTTP evidence")
    else:
        raise ValueError("HTTP status must be PASS or NOT_VERIFIED")
    return {"status": "PASS", "files_hashed": len(checksums),
            "topology": "PASS", "http": http_status,
            "fault_injection": "NOT_VERIFIED", "pcap": "NOT_VERIFIED"}


def main() -> int:
    try:
        if len(sys.argv) != 2:
            raise ValueError("usage: verify_vm_evidence.py <evidence-directory>")
        print(json.dumps(verify(Path(sys.argv[1]), SHA256), indent=2))
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        print(f"NOT_VERIFIED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
