#!/usr/bin/env bash
# verify-recovery.sh <scenario>
set -euo pipefail

SCENARIO=${1:-}
echo "=== Verify recovery for ${SCENARIO} ==="

case "$SCENARIO" in
  dns)
    echo "Testing DNS resolution..."
    kubectl run dns-test --rm -it --image=nicolaka/netshoot --restart=Never -- nslookup kubernetes.default.svc.cluster.local || true
    ;;
  service)
    echo "Testing Service access..."
    # 替换为实际 Service
    # kubectl run svc-test --rm -it --image=nicolaka/netshoot --restart=Never -- curl -sv --connect-timeout 3 http://<svc>.<ns>.svc.cluster.local
    echo "TODO: replace with actual Service endpoint"
    ;;
  network-policy)
    echo "Checking if policy is removed or allows traffic..."
    kubectl get networkpolicy -A
    ;;
  *)
    echo "No specific verification defined for ${SCENARIO}"
    ;;
esac

echo "=== Recovery verification done ==="
