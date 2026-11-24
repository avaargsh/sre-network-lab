#!/usr/bin/env python3
"""Router VM-only, fixed-scope MTU fault with pre-armed 90-second rollback."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

LAB = "sre-lab03d-vbox-intnet"
HELPER = "/usr/local/libexec/sre-lab03d-router-fault.py"
MARKER = Path("/etc/sre-network-lab/role.json")
DIR = Path("/run/sre-lab03d")
STATE = DIR / "mtu-state.json"
LOCK = DIR / "mtu.lock"
PREFIX = "SRE_LAB_FAULT="
ORIGINAL_MTU = 1500
FAULT_MTU = 1200
TTL = 90


def require_run_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,47}", value):
        raise ValueError("unsafe run id")
    return value


def require_authorization(ack, scope):
    if ack != "1" or scope != LAB:
        raise ValueError("SRE_LAB_VM_FAULT_ACK=1 and SRE_LAB_VM_SCOPE required")


def choose_private_link(context):
    if (context.get("lab") != LAB or context.get("role") != "router"
            or context.get("virtualization") != "oracle"
            or context.get("ip_forward") != "1"):
        raise ValueError("not an approved isolated VirtualBox Router")
    links = context.get("links")
    if (not isinstance(links, dict) or len(links) != 2
            or set(links.values()) != {"10.209.10.1", "10.209.20.1"}):
        raise ValueError("unrecognized Router private addresses")
    dev = next(k for k, ip in links.items() if ip == "10.209.20.1")
    if (not re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", dev)
            or context.get("default_iface") in links):
        raise ValueError("data and management interfaces overlap")
    return dev


def require_arm_preconditions(mtu, active):
    if mtu != ORIGINAL_MTU or active:
        raise ValueError("MTU must be pristine 1500 and no previous lease active")


def unit_name(run_id):
    return "sre-lab03d-mtu-" + require_run_id(run_id)


def build_arm_steps(run_id, iface, mtu):
    require_run_id(run_id)
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", iface):
        raise ValueError("unsafe interface")
    require_arm_preconditions(mtu, False)
    return (
        ("systemd-run", "--unit=" + unit_name(run_id),
         "--on-active=90s", "--timer-property=AccuracySec=1s",
         "/usr/bin/python3", HELPER, "auto-restore", run_id),
        ("ip", "link", "set", "dev", iface, "mtu", "1200"),
    )


def remote_argv(op, run_id):
    require_run_id(run_id)
    if op not in ("arm", "inspect", "restore"):
        raise ValueError("unsupported remote action")
    return ("/usr/bin/python3", HELPER, op, run_id)


def validate_state(state, context):
    if (not isinstance(state, dict)
            or state.get("iface") != choose_private_link(context)
            or state.get("original_mtu") != 1500
            or state.get("boot_id") != context.get("boot_id")):
        raise ValueError("mismatched rollback state")
    require_run_id(state.get("run_id"))


def cmd(*args, check=True):
    p = subprocess.run(args, capture_output=True, text=True, timeout=20)
    if check and p.returncode:
        raise RuntimeError(f"{args[0]} failed: {p.stderr[-500:]}")
    return p


def local_context():
    if not MARKER.is_file() or MARKER.is_symlink():
        raise ValueError("missing or unsafe VM marker")
    if json.loads(MARKER.read_text()) != {
            "lab": LAB, "role": "router", "network": "virtualbox-internal"}:
        raise ValueError("wrong VM provisioning marker")
    links = {}
    for card in json.loads(cmd("ip", "-j", "-4", "addr", "show").stdout):
        for address in card.get("addr_info", []):
            ip = address.get("local", "")
            if isinstance(ip, str) and ip.startswith("10.209."):
                if card["ifname"] in links:
                    raise ValueError("multiple lab IPs on one interface")
                links[card["ifname"]] = ip
    defaults = json.loads(cmd("ip", "-j", "-4", "route", "show", "default").stdout)
    if len(defaults) != 1:
        raise ValueError("management route ambiguous")
    return {
        "lab": LAB, "role": "router",
        "virtualization": cmd("systemd-detect-virt", "--vm").stdout.strip(),
        "ip_forward": Path("/proc/sys/net/ipv4/ip_forward").read_text().strip(),
        "links": links, "default_iface": defaults[0]["dev"],
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
    }


def current_mtu(dev):
    data = json.loads(cmd("ip", "-j", "link", "show", "dev", dev).stdout)
    if len(data) != 1 or data[0].get("ifname") != dev:
        raise ValueError("missing unique private interface")
    return int(data[0]["mtu"])


def ensure_dir():
    if DIR.is_symlink():
        raise ValueError("unsafe state directory")
    DIR.mkdir(mode=0o700, exist_ok=True)
    os.chmod(DIR, 0o700)


def load_state():
    if STATE.is_symlink():
        raise ValueError("symlink state refused")
    return json.loads(STATE.read_text()) if STATE.is_file() else None


def store_state(record):
    fd, name = tempfile.mkstemp(prefix=".mtu-", dir=DIR)
    try:
        with os.fdopen(fd, "w") as fh:
            os.fchmod(fh.fileno(), 0o600)
            json.dump(record, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(name, STATE)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def timer_active(run_id):
    return cmd("systemctl", "is-active", "--quiet",
               unit_name(run_id) + ".timer", check=False).returncode == 0


def restore(run_id, context):
    dev = choose_private_link(context)
    state = load_state()
    if state is None:
        if current_mtu(dev) != ORIGINAL_MTU:
            raise ValueError("no owned lease for nonbaseline MTU")
        return {"status": "NO_ACTIVE_LEASE", "run_id": run_id, "mtu": ORIGINAL_MTU}
    validate_state(state, context)
    if state["run_id"] != run_id:
        raise ValueError("wrong lease owner")

    # Only restore the exact value this lease injected. If another operator
    # changed the interface to an unexpected MTU, preserve it and keep the
    # lease/watchdog state for manual reconciliation instead of clobbering it.
    observed_mtu = current_mtu(dev)
    if observed_mtu not in (FAULT_MTU, ORIGINAL_MTU):
        raise ValueError(
            f"unexpected MTU {observed_mtu}; refusing to overwrite external change"
        )
    if observed_mtu == FAULT_MTU:
        cmd("ip", "link", "set", "dev", dev, "mtu", str(ORIGINAL_MTU))
        if current_mtu(dev) != ORIGINAL_MTU:
            raise RuntimeError("MTU recovery not confirmed; retain lease")
    # If it is already at baseline, make recovery idempotent without issuing
    # another link mutation. This also avoids needlessly overwriting a valid
    # operator-initiated recovery.
    STATE.unlink()
    cmd("systemctl", "stop", unit_name(run_id) + ".timer", check=False)
    return {"status": "RESTORED", "run_id": run_id, "mtu": ORIGINAL_MTU}


def arm(run_id, context):
    dev = choose_private_link(context)
    mtu = current_mtu(dev)
    require_arm_preconditions(mtu, load_state() is not None)
    if timer_active(run_id):
        raise ValueError("timer already active for run")
    timer_cmd, fault_cmd = build_arm_steps(run_id, dev, mtu)
    deadline = int(time.time()) + TTL
    store_state({"run_id": run_id, "iface": dev, "original_mtu": mtu,
                 "boot_id": context["boot_id"], "deadline_epoch": deadline})
    try:
        # Watchdog must be active before modifying any interface.
        cmd(*timer_cmd)
        if not timer_active(run_id):
            raise RuntimeError("rollback timer not active")
        cmd(*fault_cmd)
        if current_mtu(dev) != FAULT_MTU:
            raise RuntimeError("fault MTU readback failed")
    except Exception:
        # If immediate recovery fails, the timer can still retry.
        restore(run_id, context)
        raise
    return {"status": "ARMED", "run_id": run_id, "interface": dev,
            "mtu": FAULT_MTU, "deadline_epoch": deadline,
            "timer_active": True}


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in (
            "arm", "inspect", "restore", "auto-restore"):
        print("usage: router_fault.py {arm|inspect|restore|auto-restore} <run-id>", file=sys.stderr)
        return 2
    op, run_id = sys.argv[1:]
    try:
        require_run_id(run_id)
        if os.geteuid() != 0:
            raise ValueError("requires root inside dedicated Router VM")
        context = local_context()
        dev = choose_private_link(context)
        ensure_dir()
        with LOCK.open("a+") as lock:
            os.chmod(LOCK, 0o600)
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            if op == "arm":
                require_authorization(os.getenv("SRE_LAB_VM_FAULT_ACK", ""),
                                      os.getenv("SRE_LAB_VM_SCOPE", ""))
                result = arm(run_id, context)
            elif op in ("restore", "auto-restore"):
                result = restore(run_id, context)
            else:
                state = load_state()
                if state is not None:
                    validate_state(state, context)
                    if state["run_id"] != run_id:
                        raise ValueError("inspect attempted foreign lease")
                result = {"status": "ARMED" if state else "NO_ACTIVE_LEASE",
                          "run_id": run_id, "interface": dev,
                          "mtu": current_mtu(dev),
                          "timer_active": timer_active(run_id)}
                if state:
                    result["deadline_epoch"] = state["deadline_epoch"]
        print(PREFIX + json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, RuntimeError, KeyError,
            subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        print("NOT_VERIFIED: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
