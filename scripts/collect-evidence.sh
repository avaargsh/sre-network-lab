#!/usr/bin/env bash
# collect-evidence.sh <scenario> <phase>
# phase: baseline | fault | recovery
set -euo pipefail

SCENARIO=${1:-}
PHASE=${2:-}
RUN_ID=${RUN_ID:-$(date +%Y%m%d-%H%M%S)}
BASE="../../evidence/${RUN_ID}"

if [[ -z "$SCENARIO" || -z "$PHASE" ]]; then
  echo "Usage: $0 <scenario> <baseline|fault|recovery>"
  exit 1
fi

mkdir -p "${BASE}/${PHASE}"
echo "Collecting evidence for ${SCENARIO} / ${PHASE} -> ${BASE}/${PHASE}"

# 通用采集
kubectl get nodes -o wide > "${BASE}/${PHASE}/nodes.txt"
kubectl get pods -A -o wide > "${BASE}/${PHASE}/pods.txt"
kubectl get svc -A > "${BASE}/${PHASE}/services.txt"
kubectl get endpointslices -A > "${BASE}/${PHASE}/endpointslices.txt" || true

# 场景特定（可扩展）
case "$SCENARIO" in
  dns)
    kubectl -n kube-system logs -l k8s-app=kube-dns --tail=100 > "${BASE}/${PHASE}/coredns.log" || true
    ;;
  service)
    kubectl get endpointslices -A -o yaml > "${BASE}/${PHASE}/endpointslices.yaml" || true
    ;;
  network-policy)
    kubectl get networkpolicy -A -o yaml > "${BASE}/${PHASE}/networkpolicies.yaml" || true
    ;;
  envoy-503|istio-mtls)
    istioctl proxy-status > "${BASE}/${PHASE}/proxy-status.txt" 2>/dev/null || echo "istioctl not available" > "${BASE}/${PHASE}/proxy-status.txt"
    ;;
esac

# 环境快照（仅 baseline 时写一次）
if [[ "$PHASE" == "baseline" ]]; then
  cat > "${BASE}/environment.json" <<EOF
{
  "run_id": "${RUN_ID}",
  "scenario": "${SCENARIO}",
  "timestamp": "$(date -Iseconds)",
  "kubernetes": "$(kubectl version -o json 2>/dev/null | jq -r '.serverVersion.gitVersion' || echo unknown)",
  "nodes": $(kubectl get nodes -o json | jq '[.items[].metadata.name]')
}
EOF
fi

echo "Evidence collected to ${BASE}/${PHASE}"
echo "Run ID: ${RUN_ID}"
