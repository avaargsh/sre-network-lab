#!/usr/bin/env bash
# collect-evidence.sh <scenario> <baseline|fault|recovery>
# All results are locally stored and MUST be reviewed before public sharing.
set -euo pipefail
umask 077

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
SCENARIO=${1:-}
PHASE=${2:-}
case "$SCENARIO" in dns|service|mtu|network-policy|envoy-503|istio-mtls) ;; *) echo "Unsupported scenario: $SCENARIO" >&2; exit 2 ;; esac
case "$SCENARIO:$PHASE" in
  network-policy:baseline|network-policy:ingress|network-policy:egress|network-policy:additive|network-policy:recovery) ;;
  dns:baseline|dns:fault|dns:recovery|service:baseline|service:fault|service:recovery) ;;
  mtu:baseline|mtu:fault|mtu:recovery|envoy-503:baseline|envoy-503:fault|envoy-503:recovery|istio-mtls:baseline|istio-mtls:fault|istio-mtls:recovery) ;;
  *) echo "Unsupported scenario/phase: $SCENARIO/$PHASE" >&2; exit 2 ;;
esac
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
if [[ "$PHASE" != baseline ]]; then
  [[ -f "$BASE/environment.json" ]] || { echo 'Missing baseline environment.json for RUN_ID; refusing unlinked evidence' >&2; exit 2; }
  [[ "$(jq -r .scenario "$BASE/environment.json")" == "$SCENARIO" ]] || { echo 'RUN_ID belongs to a different scenario' >&2; exit 2; }
fi
mkdir -p "$OUT"
STATUS="$OUT/command-status.tsv"
LAST_RC=0
PROBE_RC=3
printf 'name\texit_code\n' > "$STATUS"

capture() {
  local name=$1 rc=0; shift
  if "$@" > "$OUT/$name.out" 2> "$OUT/$name.err"; then rc=0; else rc=$?; fi
  printf '%s\t%d\n' "$name" "$rc" >> "$STATUS"
  LAST_RC=$rc
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
  dns)
    capture coredns-logs kubectl -n kube-system logs -l k8s-app=kube-dns --tail=100
    capture dns-deployment kubectl -n sre-lab-dns get deployment lab-dns-probe -o yaml
    if [[ "$PHASE" == baseline ]]; then action=baseline
    elif [[ "$PHASE" == fault ]]; then action=fault
    else action=verify; fi
    capture lab-probe "$ROOT/scripts/lab-dns.sh" "$action"
    PROBE_RC=$LAST_RC
    ;;
  service)
    capture service-endpoints kubectl -n sre-lab-service get endpointslices -l kubernetes.io/service-name=lab-echo -o yaml
    capture service-config kubectl -n sre-lab-service get svc lab-echo -o yaml
    if [[ "$PHASE" == baseline ]]; then action=baseline
    elif [[ "$PHASE" == fault ]]; then action=fault
    else action=verify; fi
    capture lab-probe "$ROOT/scripts/lab-service.sh" "$action"
    PROBE_RC=$LAST_RC
    ;;
  network-policy)
    capture policies kubectl -n sre-lab-policy get networkpolicy -o yaml
    capture calico-ippools kubectl get ippools.crd.projectcalico.org -o yaml
    capture calico-operator kubectl get installation.operator.tigera.io default -o yaml
    capture calico-nodes kubectl -n calico-system get pods -o wide
    if [[ "$PHASE" == recovery ]]; then action=verify; else action=$PHASE; fi
    capture lab-probe "$ROOT/scripts/lab-policy.sh" "$action"
    PROBE_RC=$LAST_RC
    ;;
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
jq -n --arg scenario "$SCENARIO" --arg phase "$PHASE" --arg run_id "$RUN_ID" --argjson probe_rc "$PROBE_RC"   '{run_id:$run_id,scenario:$scenario,phase:$phase,probe_exit_code:$probe_rc,
    probe_status:(if $probe_rc==0 then "PASS" elif $probe_rc==3 then "NOT_VERIFIED" else "FAILED" end)}' > "$OUT/phase-status.json"
( cd "$OUT" && find . -maxdepth 1 -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 -r sha256sum ) > "$OUT/SHA256SUMS"
printf 'Evidence: %s\nRUN_ID: %s\nReview command-status.tsv and phase-status.json before publishing\n' "$OUT" "$RUN_ID"
if [[ "$SCENARIO" == dns || "$SCENARIO" == service || "$SCENARIO" == network-policy ]]; then
  (( PROBE_RC == 0 )) || { echo "CRITICAL: $SCENARIO $PHASE probe failed; see lab-probe.err/out" >&2; exit 1; }
fi
