#!/usr/bin/env bash
# Isolated Linux network-namespace training lab. Never run on production nodes.
set -euo pipefail

A=sre-netlab-a
B=sre-netlab-b
IP_A=10.210.10.1
IP_B=10.210.10.2

usage() { echo "Usage: $0 {up|verify|down}" >&2; exit 2; }
exists() { ip netns list | awk '{print $1}' | grep -Fxq "$1"; }
require_tools() {
  command -v ip >/dev/null || { echo 'Missing iproute2' >&2; exit 1; }
}
cleanup_created() {
  if [[ ${LINK_CREATED:-0} == 1 ]] && ip link show sre-veth-a >/dev/null 2>&1; then ip link del sre-veth-a; fi
  if [[ ${CREATED_A:-0} == 1 ]] && exists "$A"; then ip netns del "$A"; fi
  if [[ ${CREATED_B:-0} == 1 ]] && exists "$B"; then ip netns del "$B"; fi
}

case "${1:-}" in
  up)
    require_tools
    if exists "$A" || exists "$B"; then
      echo "Refusing to overwrite existing $A / $B; inspect and use 'down' deliberately." >&2
      exit 1
    fi
    CREATED_A=0; CREATED_B=0; LINK_CREATED=0
    trap cleanup_created ERR
    ip netns add "$A"; CREATED_A=1
    ip netns add "$B"; CREATED_B=1
    ip link add sre-veth-a type veth peer name sre-veth-b; LINK_CREATED=1
    ip link set sre-veth-a netns "$A"
    ip link set sre-veth-b netns "$B"
    ip -n "$A" link set sre-veth-a name eth0
    ip -n "$B" link set sre-veth-b name eth0
    ip -n "$A" addr add "$IP_A/24" dev eth0
    ip -n "$B" addr add "$IP_B/24" dev eth0
    for ns in "$A" "$B"; do
      ip -n "$ns" link set lo up
      ip -n "$ns" link set eth0 up
    done
    trap - ERR
    echo 'Namespaces created. Run: scripts/lab-netns.sh verify'
    ;;
  verify)
    require_tools
    command -v ping >/dev/null || { echo 'Missing ping' >&2; exit 1; }
    exists "$A" && exists "$B" || { echo 'Namespaces not present; run up' >&2; exit 1; }
    ip netns exec "$A" ip route get "$IP_B"
    ip netns exec "$A" ping -c 2 -W 2 "$IP_B"
    ip netns exec "$A" ip neigh show
    ip netns exec "$B" ping -c 2 -W 2 "$IP_A"
    echo 'PASS: bidirectional namespace connectivity. This is not a Kubernetes E2E test.'
    ;;
  down)
    require_tools
    if exists "$A"; then ip netns del "$A"; fi
    if exists "$B"; then ip netns del "$B"; fi
    echo 'Dedicated lab namespaces removed (if present).'
    ;;
  *) usage ;;
esac
