#!/usr/bin/env python3
"""In-guest read-only LAB-03D identity and business HTTP probe."""
import http.client
import json
from pathlib import Path
import subprocess
import sys

from lab_vm import EXPECTED, LAB, ROLES

MARKER = Path("/etc/sre-network-lab/role.json")
SRC_IP, DST_IP = "10.209.10.2", "10.209.20.2"
PREFIX = "SRE_LAB_ATTEST="


def command(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise RuntimeError(f"{args[0]}: {result.stderr[-350:]}")
    return result.stdout


def inspect(role):
    if not MARKER.is_file() or MARKER.is_symlink():
        raise ValueError("missing or unsafe Vagrant role marker")
    marker = json.loads(MARKER.read_text())
    if marker != {"lab": LAB, "role": role, "network": "virtualbox-internal"}:
        raise ValueError("this VM is not the requested isolated lab guest")
    links = {}
    for card in json.loads(command("ip", "-j", "-4", "addr", "show")):
        for addr in card.get("addr_info", []):
            ip = addr.get("local", "")
            if ip.startswith("10.209."):
                if card["ifname"] in links:
                    raise ValueError("duplicate private IPv4 address on one NIC")
                links[card["ifname"]] = ip
    if set(links.values()) != EXPECTED[role] or len(links) != len(EXPECTED[role]):
        raise ValueError("lab NIC addresses do not match strict expected topology")
    defaults = json.loads(command("ip", "-j", "-4", "route", "show", "default"))
    if len(defaults) != 1:
        raise ValueError("need exactly one management default route")
    default = defaults[0]["dev"]
    if default in links:
        raise ValueError("lab data interface is default management interface")
    result = {
        "lab": LAB, "role": role,
        "machine_id": Path("/etc/machine-id").read_text().strip(),
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "links": links, "default_iface": default,
        "virtualization": command("systemd-detect-virt", "--vm").strip(),
    }
    if role == "router":
        result["ip_forward"] = Path("/proc/sys/net/ipv4/ip_forward").read_text().strip()
    else:
        dst = DST_IP if role == "src" else SRC_IP
        paths = json.loads(command("ip", "-j", "-4", "route", "get", dst))
        if len(paths) != 1:
            raise ValueError("no unique network path to remote VM")
        path = paths[0]
        result["route"] = {
            "destination": dst, "gateway": path.get("gateway"),
            "dev": path.get("dev"), "src": path.get("prefsrc", path.get("src")),
        }
    return result


def http_probe(action):
    from http_vm import PAYLOAD, SHA256
    conn = http.client.HTTPConnection(DST_IP, 18080, timeout=12,
                                      source_address=(SRC_IP, 0))
    try:
        if action == "health":
            conn.request("GET", "/health")
        elif action == "upload":
            conn.request("POST", "/upload", PAYLOAD,
                         {"Content-Type": "application/octet-stream"})
        else:
            raise ValueError("unexpected HTTP action")
        response = conn.getresponse()
        body = response.read().decode(errors="replace")
        if action == "health":
            return {"ok": response.status == 200 and body == "healthy",
                    "status": response.status, "body": body}
        return {"ok": response.status == 200 and body == SHA256,
                "status": response.status, "digest": body}
    finally:
        conn.close()


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ("attest", "health", "upload") \
            or sys.argv[2] not in ROLES:
        print("usage: guest.py {attest|health|upload} {src|router|dst}", file=sys.stderr)
        return 2
    action, role = sys.argv[1:]
    try:
        record = inspect(role)
        if action != "attest":
            if role != "src":
                raise ValueError("only source VM may generate probes")
            record = http_probe(action)
        print(PREFIX + json.dumps(record, sort_keys=True, separators=(",", ":")))
        return 0 if action == "attest" or record.get("ok") else 1
    except (OSError, RuntimeError, ValueError, ConnectionError, TimeoutError,
            json.JSONDecodeError) as exc:
        print("NOT_VERIFIED: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
