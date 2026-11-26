#!/usr/bin/env python3
"""Independent replay of captured TCP/HTTP PMTU evidence and hash manifests."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from lab_pmtu_tcp import PHASES, assert_phase, parse_tcpdump
from verify_pmtu_evidence import read_object, verify_hashes


def verify(root: Path) -> dict:
    if not root.is_dir() or root.is_symlink():
        raise ValueError("missing or unsafe evidence directory")
    result = read_object(root / "result.json")
    if result != {"result": "PASS", "namespaces_remaining": []}:
        raise ValueError("experiment did not finish with clean namespace teardown")
    environment = read_object(root / "environment.json")
    if environment.get("scope") != "one isolated runner with three Linux namespaces, NOT separate VMs":
        raise ValueError("cannot attribute this evidence to the expected isolated lab")
    total = verify_hashes(root)
    for phase in PHASES:
        directory = root / phase
        if not directory.is_dir() or directory.is_symlink():
            raise ValueError(f"missing/unsafe phase directory: {phase}")
        status = read_object(directory / "status.json")
        if status.get("phase") != phase or status.get("status") != "PASS":
            raise ValueError(f"no phase PASS evidence for {phase}")
        for side in ("ingress", "egress"):
            if (directory / f"{side}.pcap").stat().st_size <= 40:
                raise ValueError(f"{phase}: truncated {side} pcap")
        results = json.loads((directory / "http.json").read_text())
        ingress = parse_tcpdump((directory / "ingress.decoded.txt").read_text())
        egress = parse_tcpdump((directory / "egress.decoded.txt").read_text())
        assert_phase(phase, results, ingress, egress)
        total += verify_hashes(directory)
    return {"status": "PASS", "phases": list(PHASES),
            "files_hashed": total, "scope": environment["scope"]}


if __name__ == "__main__":
    try:
        if len(sys.argv) != 2:
            raise ValueError("usage: verify_pmtu_tcp_evidence.py <evidence-root>")
        print(json.dumps(verify(Path(sys.argv[1])), indent=2))
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"NOT_VERIFIED: {exc}", file=sys.stderr)
        sys.exit(1)
