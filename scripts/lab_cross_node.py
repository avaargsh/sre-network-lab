#!/usr/bin/env python3
"""LAB-03A: prove an HTTP request traverses VXLAN between distinct Kind workers.

This is an isolated Kind/Calico experiment, NOT a VM MTU fault injector.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
NS = "sre-lab-cross-node"
OWNER_LABEL = "sre-network-lab.io/owner"
OWNER_VALUE = "cross-node-vxlan"
PODS = ("lab03-client", "lab03-server", "lab03-sniff-src", "lab03-sniff-dst")
SERVICE = "lab03-echo"
RESPONSE = "sre-network-lab-cross-node-ok"
MANIFEST = ROOT / "scenarios" / "mtu" / "cross-node.yaml"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run(*args: str, input_data: bytes | None = None, timeout: int = 150,
        check: bool = True) -> subprocess.CompletedProcess[bytes]:
    p = subprocess.run(list(args), input=input_data, capture_output=True, timeout=timeout)
    if check and p.returncode:
        raise RuntimeError(
            f"command failed rc={p.returncode}: {' '.join(args)}\n"
            + p.stderr.decode(errors="replace")[-2500:]
        )
    return p


def kc(*args: str, input_data: bytes | None = None, timeout: int = 150,
       check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return run("kubectl", *args, input_data=input_data, timeout=timeout, check=check)


def jget(*args: str) -> dict:
    return json.loads(kc(*args, "-o", "json").stdout)


def select_workers(data: dict) -> tuple[dict, dict]:
    result = []
    for node in data.get("items", []):
        meta, status = node.get("metadata", {}), node.get("status", {})
        labels = meta.get("labels") or {}
        if "node-role.kubernetes.io/control-plane" in labels or "node-role.kubernetes.io/master" in labels:
            continue
        if not any(c.get("type") == "Ready" and c.get("status") == "True"
                   for c in status.get("conditions", [])):
            continue
        ips = [a.get("address") for a in status.get("addresses", [])
               if a.get("type") == "InternalIP"]
        if len(ips) != 1 or not ips[0]:
            raise ValueError("worker missing unique InternalIP")
        ipaddress.ip_address(ips[0])
        result.append({"name": meta["name"], "ip": ips[0]})
    result.sort(key=lambda x: x["name"])
    if len(result) < 2 or result[0]["name"] == result[1]["name"] or result[0]["ip"] == result[1]["ip"]:
        raise ValueError("need two distinct Ready worker nodes with distinct InternalIPs")
    return result[0], result[1]


def require_mutation_approval(current: str, expected: str, ack: str) -> None:
    if ack != "1" or not expected.startswith("kind-") or current != expected:
        raise ValueError(
            f"mutation blocked: current context {current!r}, expected {expected!r}; "
            "check cluster and set SRE_LAB_ALLOW_MUTATIONS=1"
        )


def require_vxlan_pool(data: dict) -> None:
    pools = data.get("items", [])
    if not pools or any(
        p.get("spec", {}).get("vxlanMode") != "Always"
        or p.get("spec", {}).get("ipipMode") not in (None, "Never")
        for p in pools
    ):
        raise ValueError("Calico IPPool must have vxlanMode=Always, ipipMode=Never")


def require_pinned_nodes(actual_src: str, actual_dst: str, expected_src: str,
                         expected_dst: str) -> None:
    if actual_src != expected_src or actual_dst != expected_dst or actual_src == actual_dst:
        raise ValueError(
            f"fail closed: Pod nodes {actual_src}/{actual_dst} do not match "
            f"two pinned workers {expected_src}/{expected_dst}"
        )


def render_manifest(template: str, source: str, destination: str) -> str:
    for node in (source, destination):
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,252}", node):
            raise ValueError("invalid Kubernetes node name")
    if source == destination:
        raise ValueError("source and destination nodes must be distinct")
    return template.replace("__SOURCE_NODE__", source).replace("__DEST_NODE__", destination)


def has_vxlan_proof(decoded: str, outer_src: str, outer_dst: str,
                    pod_src: str, pod_dst: str, proof: str) -> bool:
    # tcpdump -tt emits epoch-seconds before each packet. Parse each packet
    # individually: never combine an unrelated VXLAN packet with HTTP payload.
    blocks = re.split(r"(?m)^(?=\d+\.\d+\s+IP\b)", decoded)
    outer = re.compile(r"\b" + re.escape(outer_src) +
                       r"\.\d+\s*>\s*" + re.escape(outer_dst) + r"\.4789\b")
    inner = re.compile(r"\b" + re.escape(pod_src) +
                       r"\.\d+\s*>\s*" + re.escape(pod_dst) + r"\.5678\b")
    token = "/?proof=" + proof + " "
    return any("VXLAN" in block and outer.search(block) and inner.search(block)
               and token in block for block in blocks)


def approved_context() -> None:
    current = kc("config", "current-context").stdout.decode().strip()
    require_mutation_approval(current, os.environ.get("SRE_LAB_EXPECT_CONTEXT", "kind-sre-policy-lab"),
                              os.environ.get("SRE_LAB_ALLOW_MUTATIONS", ""))


def calico_preflight() -> dict:
    kc("get", "installation.operator.tigera.io", "default", "-o", "name")
    kc("-n", "calico-system", "get", "daemonset", "calico-node", "-o", "name")
    pools = jget("get", "ippools.projectcalico.org")
    require_vxlan_pool(pools)
    return pools


def namespace_exists_and_owned() -> bool:
    p = kc("get", "namespace", NS, "-o", "json", check=False)
    if p.returncode:
        if "NotFound" in p.stderr.decode(errors="replace"):
            return False
        raise RuntimeError("cannot safely check namespace ownership: " +
                           p.stderr.decode(errors="replace"))
    data = json.loads(p.stdout)
    if data["metadata"].get("labels", {}).get(OWNER_LABEL) != OWNER_VALUE:
        raise ValueError("namespace exists but is not owned by LAB-03")
    return True


def topology() -> dict:
    workers = select_workers(jget("get", "nodes"))
    source, dest = workers
    objects = {pod: jget("-n", NS, "get", "pod", pod) for pod in PODS}
    require_pinned_nodes(
        objects["lab03-client"]["spec"]["nodeName"],
        objects["lab03-server"]["spec"]["nodeName"],
        source["name"], dest["name"],
    )
    for pod, expected in (("lab03-sniff-src", source["name"]),
                          ("lab03-sniff-dst", dest["name"])):
        if objects[pod]["spec"]["nodeName"] != expected:
            raise ValueError(f"{pod} scheduled on wrong node")
        if not objects[pod]["spec"].get("hostNetwork"):
            raise ValueError(f"{pod} must use hostNetwork for node underlay capture")
    for obj in objects.values():
        if obj.get("metadata", {}).get("deletionTimestamp") or not any(
            c.get("type") == "Ready" and c.get("status") == "True"
            for c in obj.get("status", {}).get("conditions", [])
        ):
            raise ValueError("not all four lab Pods are Ready and non-terminating")
    client = objects["lab03-client"]["status"]["podIP"]
    server = objects["lab03-server"]["status"]["podIP"]
    if client == server or not client or not server:
        raise ValueError("invalid PodIP topology")
    svc = jget("-n", NS, "get", "svc", SERVICE)
    vip = svc["spec"]["clusterIP"]
    if not vip or vip == "None":
        raise ValueError("Service ClusterIP unavailable")
    return {"source_node": source, "destination_node": dest,
            "source_pod_ip": client, "destination_pod_ip": server,
            "service_ip": vip, "pod_nodes": {k: v["spec"]["nodeName"] for k, v in objects.items()}}


def run_client(url: str) -> str:
    p = kc("-n", NS, "exec", "lab03-client", "--", "curl", "--noproxy", "*",
           "-fsS", "--connect-timeout", "3", "--max-time", "6", url, timeout=30)
    body = p.stdout.decode(errors="replace")
    if RESPONSE not in body:
        raise RuntimeError("HTTP body does not match the expected lab response")
    return body.strip()


def evidence_dir() -> Path:
    run_id = os.environ.get("RUN_ID", "")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,80}", run_id):
        raise ValueError("explicit safe RUN_ID is required for all stages")
    base = Path(os.environ.get("EVIDENCE_DIR", str(ROOT / "evidence"))) / run_id
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    return base


def save_json(path: Path, content: dict) -> None:
    path.write_text(json.dumps(content, indent=2, ensure_ascii=False) + "\n")


def checksums(directory: Path) -> None:
    lines = []
    for p in sorted(directory.iterdir()):
        if p.is_file() and p.name != "SHA256SUMS":
            lines.append(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}")
    (directory / "SHA256SUMS").write_text("\n".join(lines) + "\n")


def setup() -> None:
    approved_context()
    calico_preflight()
    workers = select_workers(jget("get", "nodes"))
    if namespace_exists_and_owned():
        existing = jget("-n", NS, "get", "pods").get("items", [])
        if existing:
            raise ValueError("dedicated namespace already has Pods; cleanup manually before setup")
    contents = render_manifest(MANIFEST.read_text(), workers[0]["name"], workers[1]["name"])
    kc("apply", "-f", "-", input_data=contents.encode())
    for name in PODS:
        kc("-n", NS, "wait", "--for=condition=Ready", "pod/" + name, "--timeout=180s",
           timeout=200)
    topology()
    print("SETUP PASS: client/server and sniffers pinned across distinct Ready worker nodes")


def verify(stage: str) -> None:
    calico_preflight()
    topo = topology()
    base = evidence_dir()
    if stage == "recheck":
        prior = json.loads((base / "baseline" / "topology.json").read_text())
        if prior != topo:
            raise ValueError("topology drift: this is not a comparable recheck")
    out = base / stage
    out.mkdir(exist_ok=True)
    save_json(out / "topology.json", topo)
    results = {
        "podip": run_client("http://" + topo["destination_pod_ip"] + ":5678/"),
        "service": run_client("http://" + topo["service_ip"] + ":80/"),
    }
    save_json(out / "request-results.json", results)
    save_json(out / "status.json", {"phase": stage, "status": "PASS",
                                   "time": now(), "cross_node": True})
    if stage == "baseline":
        save_json(base / "environment.json", {
            "run_id": os.environ["RUN_ID"], "time": now(),
            "kubernetes": jget("version"), "calico_pools": jget("get", "ippools.projectcalico.org"),
            "topology": topo, "scope": "Kind container underlay, not physical/VM MTU",
        })
    checksums(out)
    print(f"{stage.upper()} PASS: PodIP and Service VIP reachable, pinned on distinct workers")


def capture() -> None:
    calico_preflight()
    base = evidence_dir()
    prior = json.loads((base / "baseline" / "topology.json").read_text())
    topo = topology()
    if topo != prior:
        raise ValueError("topology drift since baseline")
    src_node, dst_node = topo["source_node"]["ip"], topo["destination_node"]["ip"]
    client_ip, server_ip = topo["source_pod_ip"], topo["destination_pod_ip"]
    proof = os.environ["RUN_ID"]
    out = base / "capture"
    out.mkdir(exist_ok=True)
    save_json(out / "topology.json", topo)
    procs = []
    handles = []
    try:
        for label, pod in (("source", "lab03-sniff-src"), ("destination", "lab03-sniff-dst")):
            pcap = (out / f"{label}.pcap").open("wb")
            err = (out / f"{label}.tcpdump.stderr").open("wb")
            handles.extend((pcap, err))
            cmd = ["kubectl", "-n", NS, "exec", pod, "--",
                   "timeout", "-s", "INT", "12", "tcpdump", "-i", "eth0", "-s", "0",
                   "-U", "-w", "-", "udp", "and", "src", "host", src_node,
                   "and", "dst", "host", dst_node, "and", "dst", "port", "4789"]
            procs.append(subprocess.Popen(cmd, stdout=pcap, stderr=err))
        time.sleep(2)
        responses = []
        for _ in range(15):
            responses.append(run_client("http://" + server_ip + ":5678/?proof=" + proof))
        save_json(out / "requests.json", {"proof": proof, "count": len(responses),
                                         "all_valid": True, "time": now()})
        for p in procs:
            p.wait(timeout=25)
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait()
        for handle in handles:
            handle.close()
    for label, sniff in (("source", "lab03-sniff-src"), ("destination", "lab03-sniff-dst")):
        data = (out / f"{label}.pcap").read_bytes()
        if len(data) < 40:
            raise ValueError(f"{label}: no valid captured packets; see tcpdump.stderr")
        decoded = kc("-n", NS, "exec", "-i", sniff, "--",
                     "tcpdump", "-tt", "-nn", "-vv", "-A", "-r", "-",
                     input_data=data, timeout=90).stdout.decode(errors="replace")
        (out / f"{label}.decoded.txt").write_text(decoded)
        if not has_vxlan_proof(decoded, src_node, dst_node, client_ip, server_ip, proof):
            raise ValueError(f"{label}: packet proof not found in same VXLAN packet")
    save_json(out / "status.json", {
        "phase": "capture", "status": "PASS", "proof": proof, "time": now(),
        "criteria": "both node underlay captures decode same HTTP proof inside VXLAN",
    })
    checksums(out)
    print("CAPTURE PASS: both worker underlay captures contain matching VXLAN HTTP proof")


def cleanup() -> None:
    approved_context()
    if not namespace_exists_and_owned():
        print("Nothing to clean")
        return
    kc("-n", NS, "delete", "pods", *PODS, "--ignore-not-found=true")
    kc("-n", NS, "delete", "svc", SERVICE, "--ignore-not-found=true")
    print("Removed only named lab Pods and Service; namespace retained")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["setup", "baseline", "capture", "recheck", "cleanup"])
    args = parser.parse_args()
    try:
        if args.action == "setup":
            setup()
        elif args.action in ("baseline", "recheck"):
            verify(args.action)
        elif args.action == "capture":
            capture()
        else:
            cleanup()
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired,
            KeyError, json.JSONDecodeError) as e:
        print(f"NOT_VERIFIED [{args.action}]: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
