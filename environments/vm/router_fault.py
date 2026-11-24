#!/usr/bin/env python3
"""Router VM-only, fixed-scope MTU/PMTU fault with pre-armed rollback."""
from __future__ import annotations

import fcntl
import hashlib
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
MSS = 1160
TTL = 90
SRC_IP = "10.209.10.2"
DST_IP = "10.209.20.2"


def require_run_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,47}", value):
        raise ValueError("unsafe run id")
    return value


def require_authorization(ack, scope):
    if ack != "1" or scope != LAB:
        raise ValueError("SRE_LAB_VM_FAULT_ACK=1 and SRE_LAB_VM_SCOPE required")


def choose_private_links(context):
    if (context.get("lab") != LAB or context.get("role") != "router"
            or context.get("virtualization") != "oracle"
            or context.get("ip_forward") != "1"):
        raise ValueError("not an approved isolated VirtualBox Router")
    links = context.get("links")
    if (not isinstance(links, dict) or len(links) != 2
            or set(links.values()) != {"10.209.10.1", "10.209.20.1"}):
        raise ValueError("unrecognized Router private addresses")
    by_ip = {ip: dev for dev, ip in links.items()}
    left, right = by_ip["10.209.10.1"], by_ip["10.209.20.1"]
    if (any(not re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", dev)
            for dev in (left, right))
            or context.get("default_iface") in (left, right)):
        raise ValueError("data and management interfaces overlap")
    return {"left": left, "right": right}


def choose_private_link(context):
    return choose_private_links(context)["right"]


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


def firewall_chain_names(run_id):
    digest = hashlib.sha256(require_run_id(run_id).encode()).hexdigest()[:8].upper()
    return {"icmp": "S3I" + digest, "mss": "S3M" + digest}


def firewall_tag(run_id):
    return "sre-lab03d:" + require_run_id(run_id)


def blackhole_rule_specs(run_id, context):
    links = choose_private_links(context)
    names = firewall_chain_names(run_id)
    tag = firewall_tag(run_id)
    icmp_rule = (
        "-o", links["left"], "-p", "icmp", "--icmp-type",
        "fragmentation-needed", "-d", SRC_IP,
        "-m", "comment", "--comment", tag, "-j", "DROP",
    )
    forward = (
        "-i", links["left"], "-o", links["right"], "-p", "tcp",
        "--tcp-flags", "SYN,RST", "SYN", "-s", SRC_IP, "-d", DST_IP,
        "-m", "comment", "--comment", tag, "-j", "TCPMSS",
        "--set-mss", str(MSS),
    )
    reverse = (
        "-i", links["right"], "-o", links["left"], "-p", "tcp",
        "--tcp-flags", "SYN,RST", "SYN", "-s", DST_IP, "-d", SRC_IP,
        "-m", "comment", "--comment", tag, "-j", "TCPMSS",
        "--set-mss", str(MSS),
    )
    return {
        "tag": tag,
        "icmp": {
            "table": "filter", "parent": "OUTPUT", "chain": names["icmp"],
            "rules": (icmp_rule,),
            "jump": ("-m", "comment", "--comment", tag, "-j", names["icmp"]),
        },
        "mss": {
            "table": "mangle", "parent": "FORWARD", "chain": names["mss"],
            "rules": (forward, reverse),
            "jump": ("-m", "comment", "--comment", tag, "-j", names["mss"]),
        },
    }


def remote_argv(op, run_id):
    require_run_id(run_id)
    if op not in ("arm", "arm-blackhole", "mitigate", "inspect", "restore"):
        raise ValueError("unsupported remote action")
    return ("/usr/bin/python3", HELPER, op, run_id)


def validate_state(state, context):
    if (not isinstance(state, dict)
            or state.get("iface") != choose_private_link(context)
            or state.get("original_mtu") != ORIGINAL_MTU
            or state.get("boot_id") != context.get("boot_id")):
        raise ValueError("mismatched rollback state")
    run_id = require_run_id(state.get("run_id"))
    mode = state.get("mode", "mtu-only")
    if mode == "mtu-only":
        return
    if mode != "tcp-blackhole":
        raise ValueError("unknown Router fault lease mode")
    links = choose_private_links(context)
    specs = blackhole_rule_specs(run_id, context)
    if (state.get("left_iface") != links["left"]
            or state.get("icmp_chain") != specs["icmp"]["chain"]
            or state.get("firewall_tag") != specs["tag"]
            or state.get("mss_chain") not in (None, specs["mss"]["chain"])):
        raise ValueError("blackhole lease does not match this Router/run scope")


def cmd(*args, check=True):
    p = subprocess.run(args, capture_output=True, text=True, timeout=20)
    if check and p.returncode:
        raise RuntimeError(f"{args[0]} failed: {p.stderr[-500:]}")
    return p


def ipt(table, *args, check=True):
    return cmd("iptables", "-w", "5", "-t", table, *args, check=check)


def chain_exists(table, chain):
    return ipt(table, "-S", chain, check=False).returncode == 0


def rule_exists(table, chain, *rule):
    return ipt(table, "-C", chain, *rule, check=False).returncode == 0


def ensure_no_orphaned_lab_rules():
    """Refuse a new lease if a prior run left any lab-owned firewall state."""
    for table in ("filter", "mangle"):
        result = ipt(table, "-S")
        for line in result.stdout.splitlines():
            if (re.search(r"^-N S3[IM][A-F0-9]{8}$", line)
                    or "sre-lab03d:" in line):
                raise ValueError("orphaned LAB-03D firewall state; manual review required")


def add_owned_chain(table, parent, chain, tag, rules):
    """Create one unique chain; the owner marker makes cleanup non-ambiguous."""
    if chain_exists(table, chain):
        raise ValueError("run-scoped firewall chain already exists")
    jump = ("-m", "comment", "--comment", tag, "-j", chain)
    if rule_exists(table, parent, *jump):
        raise ValueError("run-scoped firewall jump already exists")
    ipt(table, "-N", chain)
    marker = ("-m", "comment", "--comment", tag, "-j", "RETURN")
    try:
        # Put the ownership marker at the tail; functional rules are inserted
        # above it so the marker never short-circuits the chain.
        ipt(table, "-A", chain, *marker)
        for rule in rules:
            ipt(table, "-I", chain, "1", *rule)
        ipt(table, "-I", parent, "1", *jump)
    except Exception:
        try:
            remove_owned_chain(table, parent, chain, tag, rules)
        except Exception:
            # Preserve the active lease and watchdog if cleanup is uncertain.
            pass
        raise


def remove_owned_chain(table, parent, chain, tag, rules):
    """Remove only a chain carrying this run's marker and exact jump/rules."""
    jump = ("-m", "comment", "--comment", tag, "-j", chain)
    marker = ("-m", "comment", "--comment", tag, "-j", "RETURN")
    exists = chain_exists(table, chain)
    if not exists:
        if rule_exists(table, parent, *jump):
            raise ValueError("firewall jump exists without its owned chain")
        return False
    if not rule_exists(table, chain, *marker):
        raise ValueError("refuse to remove firewall chain without owner marker")
    if rule_exists(table, parent, *jump):
        ipt(table, "-D", parent, *jump)
    for rule in rules:
        if rule_exists(table, chain, *rule):
            ipt(table, "-D", chain, *rule)
    if rule_exists(table, chain, *marker):
        ipt(table, "-D", chain, *marker)
    remaining = ipt(table, "-S", chain).stdout.splitlines()
    if any(line.startswith("-A ") for line in remaining):
        raise ValueError("foreign or unexpected rules remain in owned firewall chain")
    ipt(table, "-X", chain)
    return True


def chain_packet_count(table, chain):
    result = ipt(table, "-L", chain, "-v", "-x", "-n", check=False)
    if result.returncode:
        return 0
    total = 0
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[0].isdigit() and fields[2] != "RETURN":
            total += int(fields[0])
    return total


def firewall_report(state, context):
    specs = blackhole_rule_specs(state["run_id"], context)
    icmp = specs["icmp"]
    icmp_active = (
        chain_exists(icmp["table"], icmp["chain"])
        and rule_exists(icmp["table"], icmp["parent"], *icmp["jump"])
        and all(rule_exists(icmp["table"], icmp["chain"], *r)
                for r in icmp["rules"])
    )
    mss = specs["mss"]
    mss_active = False
    if state.get("mss_chain") is not None:
        mss_active = (
            chain_exists(mss["table"], mss["chain"])
            and rule_exists(mss["table"], mss["parent"], *mss["jump"])
            and all(rule_exists(mss["table"], mss["chain"], *r)
                    for r in mss["rules"])
        )
    return {
        "icmp_drop_active": icmp_active,
        "icmp_drop_packets": chain_packet_count(icmp["table"], icmp["chain"]),
        "mss_active": mss_active,
        "mss_packets": chain_packet_count(mss["table"], mss["chain"])
            if state.get("mss_chain") is not None else 0,
    }


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
        return {"status": "NO_ACTIVE_LEASE", "run_id": run_id,
                "mtu": ORIGINAL_MTU}
    validate_state(state, context)
    if state["run_id"] != run_id:
        raise ValueError("wrong lease owner")
    cmd("ip", "link", "set", "dev", dev, "mtu", str(ORIGINAL_MTU))
    if current_mtu(dev) != ORIGINAL_MTU:
        raise RuntimeError("MTU recovery not confirmed; retain lease")

    icmp_removed = False
    mss_removed = False
    if state.get("mode") == "tcp-blackhole":
        specs = blackhole_rule_specs(run_id, context)
        if state.get("mss_chain") is not None:
            mss = specs["mss"]
            mss_removed = remove_owned_chain(
                mss["table"], mss["parent"], mss["chain"],
                specs["tag"], mss["rules"])
        icmp = specs["icmp"]
        icmp_removed = remove_owned_chain(
            icmp["table"], icmp["parent"], icmp["chain"],
            specs["tag"], icmp["rules"])

    STATE.unlink()
    cmd("systemctl", "stop", unit_name(run_id) + ".timer", check=False)
    return {"status": "RESTORED", "run_id": run_id, "mtu": ORIGINAL_MTU,
            "icmp_drop_removed": icmp_removed, "mss_rule_removed": mss_removed}


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


def arm_blackhole(run_id, context):
    links = choose_private_links(context)
    dev = links["right"]
    mtu = current_mtu(dev)
    require_arm_preconditions(mtu, load_state() is not None)
    if timer_active(run_id):
        raise ValueError("timer already active for run")
    ensure_no_orphaned_lab_rules()
    specs = blackhole_rule_specs(run_id, context)
    for item in (specs["icmp"], specs["mss"]):
        if chain_exists(item["table"], item["chain"]) or \
                rule_exists(item["table"], item["parent"], *item["jump"]):
            raise ValueError("run-scoped firewall resources already exist")
    timer_cmd, fault_cmd = build_arm_steps(run_id, dev, mtu)
    deadline = int(time.time()) + TTL
    state = {
        "run_id": run_id, "iface": dev, "original_mtu": mtu,
        "boot_id": context["boot_id"], "deadline_epoch": deadline,
        "mode": "tcp-blackhole", "left_iface": links["left"],
        "icmp_chain": specs["icmp"]["chain"], "mss_chain": None,
        "firewall_tag": specs["tag"],
    }
    store_state(state)
    try:
        # The independent rollback timer is active before firewall or MTU changes.
        cmd(*timer_cmd)
        if not timer_active(run_id):
            raise RuntimeError("rollback timer not active")
        icmp = specs["icmp"]
        add_owned_chain(icmp["table"], icmp["parent"], icmp["chain"],
                        specs["tag"], icmp["rules"])
        cmd(*fault_cmd)
        if current_mtu(dev) != FAULT_MTU:
            raise RuntimeError("blackhole MTU readback failed")
        report = firewall_report(state, context)
        if not report["icmp_drop_active"]:
            raise RuntimeError("ICMP Fragmentation Needed drop rule did not verify")
    except Exception:
        # Preserve the timer if immediate cleanup cannot be confirmed.
        restore(run_id, context)
        raise
    return {
        "status": "BLACKHOLE_ARMED", "run_id": run_id,
        "interface": dev, "mtu": FAULT_MTU,
        "deadline_epoch": deadline, "timer_active": True,
        "icmp_drop_active": True, "mss_active": False,
    }


def mitigate(run_id, context):
    state = load_state()
    if state is None:
        raise ValueError("no active owned blackhole lease")
    validate_state(state, context)
    if state.get("run_id") != run_id or state.get("mode") != "tcp-blackhole":
        raise ValueError("MSS mitigation requires this run's blackhole lease")
    if current_mtu(state["iface"]) != FAULT_MTU or not timer_active(run_id):
        raise ValueError("blackhole MTU or rollback watchdog is no longer active")
    if not firewall_report(state, context)["icmp_drop_active"]:
        raise ValueError("owned ICMP blackhole rule is not active")
    if state.get("mss_chain") is not None:
        raise ValueError("MSS mitigation already active")
    specs = blackhole_rule_specs(run_id, context)
    mss = specs["mss"]
    state["mss_chain"] = mss["chain"]
    store_state(state)
    try:
        add_owned_chain(mss["table"], mss["parent"], mss["chain"],
                        specs["tag"], mss["rules"])
    except Exception:
        restore(run_id, context)
        raise
    report = firewall_report(state, context)
    if not report["mss_active"] or not report["icmp_drop_active"]:
        restore(run_id, context)
        raise RuntimeError("MSS mitigation or ICMP blackhole rule did not verify")
    return {
        "status": "MITIGATED", "run_id": run_id, "mtu": FAULT_MTU,
        "deadline_epoch": state["deadline_epoch"], "timer_active": True,
        **report,
    }


def inspect(run_id, context):
    state = load_state()
    if state is None:
        dev = choose_private_link(context)
        if current_mtu(dev) != ORIGINAL_MTU:
            raise ValueError("no owned lease for nonbaseline MTU")
        ensure_no_orphaned_lab_rules()
        specs = blackhole_rule_specs(run_id, context)
        clean = True
        for item in (specs["icmp"], specs["mss"]):
            if chain_exists(item["table"], item["chain"]) or \
                    rule_exists(item["table"], item["parent"], *item["jump"]):
                clean = False
        if not clean:
            raise ValueError("orphaned run-scoped firewall resources remain")
        return {"status": "NO_ACTIVE_LEASE", "run_id": run_id,
                "interface": dev, "mtu": ORIGINAL_MTU,
                "timer_active": timer_active(run_id), "firewall_clean": True}

    validate_state(state, context)
    if state["run_id"] != run_id:
        raise ValueError("inspect attempted foreign lease")
    mtu = current_mtu(state["iface"])
    active = timer_active(run_id)
    if state.get("mode") != "tcp-blackhole":
        return {"status": "ARMED", "run_id": run_id,
                "interface": state["iface"], "mtu": mtu,
                "timer_active": active, "deadline_epoch": state["deadline_epoch"]}
    report = firewall_report(state, context)
    if not report["icmp_drop_active"]:
        raise ValueError("owned ICMP blackhole rule is missing")
    if state.get("mss_chain") is not None and not report["mss_active"]:
        raise ValueError("owned MSS mitigation rule is missing")
    return {
        "status": "MITIGATED" if state.get("mss_chain") else "BLACKHOLE_ARMED",
        "run_id": run_id, "interface": state["iface"], "mtu": mtu,
        "timer_active": active, "deadline_epoch": state["deadline_epoch"],
        **report,
    }


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in (
            "arm", "arm-blackhole", "mitigate", "inspect", "restore", "auto-restore"):
        print("usage: router_fault.py {arm|arm-blackhole|mitigate|inspect|restore|auto-restore} <run-id>", file=sys.stderr)
        return 2
    op, run_id = sys.argv[1:]
    try:
        require_run_id(run_id)
        if os.geteuid() != 0:
            raise ValueError("requires root inside dedicated Router VM")
        context = local_context()
        choose_private_links(context)
        ensure_dir()
        with LOCK.open("a+") as lock:
            os.chmod(LOCK, 0o600)
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            if op in ("arm", "arm-blackhole"):
                require_authorization(os.getenv("SRE_LAB_VM_FAULT_ACK", ""),
                                      os.getenv("SRE_LAB_VM_SCOPE", ""))
                result = arm(run_id, context) if op == "arm" else arm_blackhole(run_id, context)
            elif op == "mitigate":
                result = mitigate(run_id, context)
            elif op in ("restore", "auto-restore"):
                result = restore(run_id, context)
            else:
                result = inspect(run_id, context)
        print(PREFIX + json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, RuntimeError, KeyError,
            subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        print("NOT_VERIFIED: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
