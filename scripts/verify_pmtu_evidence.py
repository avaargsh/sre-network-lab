#!/usr/bin/env python3
"""Independent offline auditor for LAB-03B's real kernel PMTU evidence."""
import hashlib
import json
from pathlib import Path
import re
import sys

from lab_pmtu import check_phase, valid_pcap_size

PHASES = ("baseline", "blackhole", "discovery", "recovery")


def verify_hashes(folder: Path) -> int:
    sums = folder / "SHA256SUMS"
    if not sums.is_file():
        raise ValueError(f"missing {sums}")
    expected = {}
    for line in sums.read_text().splitlines():
        match = re.fullmatch(r"([a-f0-9]{64})  ([A-Za-z0-9_.-]+)", line)
        if not match or match.group(2) == "SHA256SUMS" or match.group(2) in expected:
            raise ValueError(f"invalid / repeated checksum entry: {line}")
        expected[match.group(2)] = match.group(1)
    files = {p.name for p in folder.iterdir() if p.is_file() and p.name != "SHA256SUMS"}
    if not files or files != set(expected):
        raise ValueError(f"missing/unaccounted files in {folder}: expected {set(expected)}, actual {files}")
    for name, digest in expected.items():
        if hashlib.sha256((folder / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"SHA-256 mismatch: {folder / name}")
    return len(expected)


def read_object(path: Path) -> dict:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def verify(root: Path) -> dict:
    if not root.is_dir() or root.is_symlink():
        raise ValueError("evidence root missing or a symlink")
    result = read_object(root / "result.json")
    if result.get("result") != "PASS" or result.get("namespaces_remaining") != []:
        raise ValueError(f"lab not cleanly recovered: {result}")
    env = read_object(root / "environment.json")
    if not env.get("scope", "").startswith("one isolated VM/runner"):
        raise ValueError("unexpected lab scope")
    hashes = verify_hashes(root)
    phases = {}
    for phase in PHASES:
        folder = root / phase
        if not folder.is_dir():
            raise ValueError(f"missing phase {phase}")
        status = read_object(folder / "status.json")
        if status.get("phase") != phase or status.get("status") != "PASS":
            raise ValueError(f"{phase}: missing PASS evidence")
        pings = {}
        for kind in (("small", "large") if phase == "blackhole" else ("large",)):
            output = (folder / f"{kind}.ping.txt").read_text()
            match = re.search(r"(?m)^exit_code: (\d+)$", output)
            if not match:
                raise ValueError(f"{phase}/{kind}: missing actual ping exit code")
            pings[kind] = (int(match.group(1)), output)
        ingress = (folder / "ingress.decoded.txt").read_text()
        egress = (folder / "egress.decoded.txt").read_text()
        check_phase(phase, pings, ingress, egress)
        for side in ("ingress", "egress"):
            if not valid_pcap_size(phase, side, (folder / f"{side}.pcap").stat().st_size):
                raise ValueError(f"{phase}: empty/truncated {side} pcap")
        phases[phase] = {"status": "PASS", "files_hashed": verify_hashes(folder)}
        hashes += phases[phase]["files_hashed"]
    return {"status": "PASS", "scope": env["scope"],
            "phase_count": len(phases), "checksums_verified": hashes, "phases": phases}


if __name__ == "__main__":
    try:
        if len(sys.argv) != 2:
            raise ValueError("Usage: verify_pmtu_evidence.py <evidence-directory>")
        report = verify(Path(sys.argv[1]))
        print(json.dumps(report, indent=2))
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
        print("NOT_VERIFIED: " + str(exc), file=sys.stderr)
        sys.exit(1)
