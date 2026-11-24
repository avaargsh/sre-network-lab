#!/usr/bin/env bash
# LAB-03D true three-VirtualBox-VM baseline/PCAP E2E with separately confirmed optional watchdog fault.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VM_DIR="$ROOT/environments/vm"
created_by_this_run=0

cleanup() {
  local code=$?
  trap - EXIT
  if [[ "$created_by_this_run" == "1" ]]; then
    # Preflight refuses pre-existing Vagrant state; only owned VMs are cleaned.
    if ! (cd "$VM_DIR" && vagrant destroy -f); then
      echo "NOT_VERIFIED: cleanup failed; inspect dedicated host" >&2
      code=1
    fi
  fi
  exit "$code"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [[ "${SRE_LAB_LIVE_E2E_ACK:-}" != "I_CONFIRM_DEDICATED_VBOX_HOST" ]] ||
   [[ "${SRE_LAB_VM_SCOPE:-}" != "sre-lab03d-vbox-intnet" ]]; then
  echo "REFUSED: explicit dedicated-host and lab-scope approval required" >&2
  exit 3
fi
watchdog_ack="${SRE_LAB_VM_WATCHDOG_ACK:-SKIP}"
if [[ -n "$watchdog_ack" && "$watchdog_ack" != "SKIP" &&
      "$watchdog_ack" != "I_CONFIRM_ROUTER_MTU_WATCHDOG" ]]; then
  echo "REFUSED: invalid Router MTU watchdog confirmation" >&2
  exit 3
fi
if [[ "$(id -u)" == "0" ]]; then
  echo "REFUSED: never run Vagrant as root on the host" >&2
  exit 3
fi
if [[ -n "${VAGRANT_CWD:-}" || -n "${VAGRANT_DOTFILE_PATH:-}" ]]; then
  echo "REFUSED: external Vagrant workspace/state overrides not allowed" >&2
  exit 3
fi
for tool in VBoxManage vagrant python3 awk git; do
  command -v "$tool" >/dev/null ||
    { echo "REFUSED: $tool unavailable" >&2; exit 3; }
done
if [[ -e "$VM_DIR/.vagrant" ]]; then
  echo "REFUSED: existing Vagrant state; clean dedicated workspace required" >&2
  exit 3
fi
if [[ -n "$(VBoxManage list runningvms)" ]]; then
  echo "REFUSED: unrelated VirtualBox VMs already running" >&2
  exit 3
fi

RUN_ID="vm-live-${GITHUB_RUN_ID:-$(date -u +%Y%m%d%H%M%S)}-${GITHUB_RUN_ATTEMPT:-1}"
cd "$VM_DIR"
state="$(vagrant status --machine-readable)"
for role in src router dst; do
  if ! awk -F, -v role="$role" \
    '$2==role && $3=="state" && $4=="not_created" {seen=1} END {exit !seen}' <<< "$state"; then
    echo "REFUSED: $role is not in not_created state" >&2
    exit 3
  fi
done
cd "$ROOT"

# Set ownership before 'up': partially-created guests are still ours.
created_by_this_run=1
(cd "$VM_DIR" && vagrant up --provider=virtualbox)
python3 environments/vm/lab_vm.py baseline --run-id "$RUN_ID-baseline"
python3 environments/vm/verify_vm_evidence.py "evidence/$RUN_ID-baseline"
python3 environments/vm/vm_pcap_baseline.py baseline-pcap --run-id "$RUN_ID-pcap"
python3 environments/vm/verify_vm_pcap.py "evidence/$RUN_ID-pcap"

echo "REAL_VM_BASELINE_AND_PCAP_PASS: $RUN_ID"
if [[ "$watchdog_ack" == "I_CONFIRM_ROUTER_MTU_WATCHDOG" ]]; then
  # This is a second, explicit approval. The watchdog helper changes only the
  # Router's private 10.209.20.1 interface and waits beyond its 90s lease.
  export SRE_LAB_VM_FAULT_ACK=1
  export SRE_LAB_VM_SCOPE=sre-lab03d-vbox-intnet
  WATCHDOG_RUN_ID="${RUN_ID}-watchdog"
  python3 environments/vm/vm_mtu_drill.py drill --rollback-mode watchdog --run-id "$WATCHDOG_RUN_ID"
  python3 environments/vm/verify_vm_fault.py "evidence/$WATCHDOG_RUN_ID"
  echo "REAL_VM_WATCHDOG_PASS: $WATCHDOG_RUN_ID"
else
  echo "REAL_VM_WATCHDOG_NOT_RUN: second confirmation was not supplied"
fi
