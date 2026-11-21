#!/usr/bin/env bash
# collect-evidence.sh <scenario> <baseline|fault|recovery>
# All results are locally stored and MUST be reviewed before public sharing.
set -euo pipefail
umask 077

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
SCENARIO=${1:-}
PHASE=${2:-}
case "$SCENARIO" in dns|service|mtu|network-policy|envoy-503|istio-mtls) ;; *) echo "Unsupported scenario: $SCENARIO" >&2; exit 2 ;; esac
case "$PHASE" in baseline|fault|recovery) ;; *) echo "Usage: $0 <scenario> <baseline|fault|recovery>" >&2; exit 2 ;; esac
command -v kubectl >/dev/null || { echo 'kubectl required' >&2; exit 1; }
command -v jq >/dev/null || { echo 'jq required' >&2; exit 1; }

if [[ -z ${RUN_ID:-} ]]; then
  if [[ "$PHASE" != baseline ]]; then
    echo 'RUN_ID is required for fault and recovery phases (reuse the baseline RUN_ID)' >&2
    exit 2
  fi
  RUN_ID="${SCENARIO}-$(date -u +%Y%m%dT%H%M%SZ)"
fi
[[ "$RUN_ID" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,100}$ ]] || { echo 'Invalid RUN_ID' >&2; exit 2; }
BASE="${EVIDENCE_DIR:-$ROOT/evidence}/$RUN_ID"
OUT="$BASE/$PHASE"
mkdir -p "$OUT"
STATUS="$OUT/command-status.tsv"
printf 'name\texit_code\n' > "$STATUS"

capture() {
  local name=$1 rc=0; shift
  if "$@" > "$OUT/$name.out" 2> "$OUT/$name.err"; then rc=0; else rc=$?; fi
  printf '%s\t%d\n' "$name" "$rc" >> "$STATUS"
  if ((rc != 0)); then echo "WARN $name exited $rc" >&2; fi
}

# Safety: fail before collecting anything when kubectl cannot reach the cluster.
kubectl cluster-info >/dev/null
capture nodes kubectl get nodes -o wide
capture pods kubectl get pods -A -o wide
capture services kubectl get svc -A -o wide
capture endpointslices kubectl get endpointslices -A -o wide
capture networkpolicies kubectl get networkpolicy -A
capture kube-proxy kubectl -n kube-system get cm kube-proxy -o yaml
capture cni-pods kubectl -n kube-system get pods -o wide

case "$SCENARIO" in
  dns) capture coredns-logs kubectl -n kube-system logs -l k8s-app=kube-dns --tail=100 ;;
  service) capture service-endpoints kubectl -n sre-lab-service get endpointslices -l kubernetes.io/service-name=lab-echo -o yaml ;;
  network-policy) capture policies kubectl get networkpolicy -A -o yaml ;;
  envoy-503|istio-mtls)
    if command -v istioctl >/dev/null; then capture proxy-status istioctl proxy-status; else echo -e 'proxy-status\t127' >> "$STATUS"; fi
    ;;
esac

if [[ "$PHASE" == baseline ]]; then
  kubectl version -o json > "$OUT/kubernetes-version.json"
  kubectl get nodes -o json > "$OUT/nodes.json"
  jq -n \
    --arg id "$RUN_ID" --arg scenario "$SCENARIO" \
    --arg timestamp "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --arg kernel "$(uname -r)" \
    --slurpfile version "$OUT/kubernetes-version.json" \
    --slurpfile nodes "$OUT/nodes.json" \
    '{run_id:$id, scenario:$scenario, timestamp:$timestamp, collector_kernel:$kernel,
      kubernetes:($version[0].serverVersion.gitVersion // "unknown"),
      nodes:[$nodes[0].items[]? | {name:.metadata.name, kernel:(.status.nodeInfo.kernelVersion // "unknown"),
      kubelet:(.status.nodeInfo.kubeletVersion // "unknown")}],
      cni:"REQUIRES_MANUAL_CONFIRMATION", kube_proxy_mode:"REQUIRES_MANUAL_CONFIRMATION"}' > "$BASE/environment.json"
fi
printf 'Evidence: %s\nRUN_ID: %s\nReview before publishing. Failed optional commands are in %s\n' "$OUT" "$RUN_ID" "$STATUS"
