#!/usr/bin/env bash
# LAB-04: dedicated Calico NetworkPolicy allow/deny matrix; never mutates cluster-wide policies.
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "$0")/.." && pwd)
NS=sre-lab-policy
D="$ROOT/scenarios/network-policy"
EXPECTED=sre-network-lab-policy-ok
ATTEMPTS=$(printenv LAB_ATTEMPTS || printf 12)
DELAY=$(printenv LAB_RETRY_DELAY || printf 2)
[[ "$ATTEMPTS" =~ ^[1-9][0-9]*$ && "$DELAY" =~ ^[0-9]+$ ]] || { echo 'Invalid retry configuration' >&2; exit 2; }
usage() { echo "Usage: $0 {setup|baseline|inject-ingress|ingress|inject-egress|egress|inject-additive|additive|restore|verify|cleanup}" >&2; exit 2; }
need() { command -v kubectl >/dev/null && command -v jq >/dev/null || { echo 'kubectl and jq required' >&2; exit 1; }; }
mutation_gate() {
  [[ "$(printenv SRE_LAB_ALLOW_MUTATIONS || true)" == 1 ]] || { echo 'Set SRE_LAB_ALLOW_MUTATIONS=1 for an isolated test cluster' >&2; exit 3; }
  local expected current
  expected=$(printenv SRE_LAB_EXPECT_CONTEXT || printf 'kind-sre-policy-lab')
  current=$(kubectl config current-context)
  [[ "$current" == "$expected" ]] || { echo "Refusing mutation: context $current != expected $expected" >&2; exit 3; }
}
require_calico() {
  kubectl get installation.operator.tigera.io default -o name >/dev/null || { echo 'Calico Installation missing; do not use kindnet' >&2; exit 1; }
  kubectl -n calico-system get daemonset calico-node -o name >/dev/null || { echo 'Calico daemonset missing' >&2; exit 1; }
}
names() { kubectl -n "$NS" get networkpolicies -o json | jq -r '[.items[].metadata.name] | sort | join(",")'; }
expect_policies() {
  local actual
  actual=$(names)
  [[ "$actual" == "$1" ]] || { echo "Unexpected policies [$actual]; expected [$1]" >&2; exit 1; }
}
ready_pod() {
  kubectl -n "$NS" get pods -l "app=$1" -o json | jq -r '
    [.items[] | select(.metadata.deletionTimestamp == null)
      | select(.status.phase == "Running")
      | select(any(.status.conditions[]?; .type == "Ready" and .status == "True"))
      | .metadata.name] | last // empty'
}
prepare() {
  SERVER=$(ready_pod lab-policy-server)
  ALLOWED=$(ready_pod lab-policy-allowed)
  BLOCKED=$(ready_pod lab-policy-blocked)
  [[ -n "$SERVER" && -n "$ALLOWED" && -n "$BLOCKED" ]] || { echo 'Ready test Pods missing' >&2; exit 1; }
  POD_IP=$(kubectl -n "$NS" get pod "$SERVER" -o jsonpath='{.status.podIP}')
  VIP=$(kubectl -n "$NS" get svc lab-policy-server -o jsonpath='{.spec.clusterIP}')
  [[ -n "$POD_IP" && -n "$VIP" && "$VIP" != None ]] || { echo 'Backend IP or VIP missing' >&2; exit 1; }
}
# Only accept curl 7/28 for denied TCP connections; never 6 (DNS) or HTTP 4xx/5xx.
probe() {
  local label=$1 client=$2 expectation=$3 url=$4 n rc=0 output='' consecutive=0
  for ((n=1; n<=ATTEMPTS; n++)); do
    if output=$(kubectl -n "$NS" exec "$client" -- curl --noproxy '*' -fsS --connect-timeout 2 --max-time 3 "$url" 2>&1); then rc=0; else rc=$?; fi
    printf 'PROBE=%s EXPECTED=%s TRY=%d RC=%d OUTPUT=%q\n' "$label" "$expectation" "$n" "$rc" "$output"
    if [[ "$expectation" == allow ]]; then
      [[ "$rc" -eq 0 && "$output" == *"$EXPECTED"* ]] && { echo "PASS $label allowed"; return 0; }
    elif [[ "$rc" -eq 7 || "$rc" -eq 28 ]]; then
      consecutive=$((consecutive+1))
      ((consecutive >= 2)) && { echo "PASS $label denied curl_rc=$rc"; return 0; }
    else
      consecutive=0
    fi
    sleep "$DELAY"
  done
  echo "FAIL $label expected $expectation; last rc=$rc" >&2
  return 1
}
matrix() {
  local stage=$1 blocked_expectation=allow
  [[ "$stage" == ingress || "$stage" == egress ]] && blocked_expectation=deny
  prepare
  probe allowed-podip "$ALLOWED" allow "http://$POD_IP:5678/"
  probe blocked-podip "$BLOCKED" "$blocked_expectation" "http://$POD_IP:5678/"
  probe allowed-service "$ALLOWED" allow "http://$VIP:80/"
  probe blocked-service "$BLOCKED" "$blocked_expectation" "http://$VIP:80/"
}
remove_lab_policies() {
  kubectl -n "$NS" delete networkpolicy lab-deny-ingress lab-allow-allowed lab-allow-blocked lab-deny-egress-blocked --ignore-not-found=true
}
need
(( $# == 1 )) || usage
case "$1" in
  setup|inject-ingress|inject-egress|inject-additive|restore|cleanup) mutation_gate ;;
esac
case "$1" in
  setup)
    require_calico
    if kubectl get namespace "$NS" -o name >/dev/null 2>&1; then
      owner=$(kubectl get namespace "$NS" -o jsonpath='{.metadata.labels.sre-network-lab\.io/owner}')
      [[ "$owner" == policy-lab ]] || { echo 'Existing namespace is not owned by this lab' >&2; exit 1; }
    fi
    kubectl apply -f "$D/manifests.yaml"
    kubectl -n "$NS" rollout status deployment/lab-policy-server --timeout=150s
    kubectl -n "$NS" rollout status deployment/lab-policy-allowed --timeout=150s
    kubectl -n "$NS" rollout status deployment/lab-policy-blocked --timeout=150s
    expect_policies ''
    ;;
  baseline|verify) require_calico; expect_policies ''; matrix baseline ;;
  inject-ingress)
    require_calico; expect_policies ''
    kubectl apply -f "$D/ingress.yaml"
    ;;
  ingress)
    require_calico; expect_policies 'lab-allow-allowed,lab-deny-ingress'
    matrix ingress
    ;;
  inject-egress)
    require_calico; expect_policies 'lab-allow-allowed,lab-deny-ingress'
    kubectl -n "$NS" delete networkpolicy lab-deny-ingress lab-allow-allowed
    kubectl apply -f "$D/egress.yaml"
    ;;
  egress)
    require_calico; expect_policies 'lab-deny-egress-blocked'
    matrix egress
    ;;
  inject-additive)
    require_calico; expect_policies 'lab-deny-egress-blocked'
    kubectl -n "$NS" delete networkpolicy lab-deny-egress-blocked
    kubectl apply -f "$D/ingress.yaml"
    kubectl apply -f "$D/allow-blocked.yaml"
    ;;
  additive)
    require_calico; expect_policies 'lab-allow-allowed,lab-allow-blocked,lab-deny-ingress'
    matrix additive
    ;;
  restore)
    require_calico
    current=$(names)
    for name in $(printf '%s' "$current" | tr ',' ' '); do
      case "$name" in lab-deny-ingress|lab-allow-allowed|lab-allow-blocked|lab-deny-egress-blocked) ;; *)
        echo "Foreign NetworkPolicy $name present; refusing restore" >&2; exit 1 ;;
      esac
    done
    remove_lab_policies
    ;;
  cleanup)
    require_calico
    remove_lab_policies
    kubectl -n "$NS" delete deployment/lab-policy-server deployment/lab-policy-allowed deployment/lab-policy-blocked svc/lab-policy-server --ignore-not-found=true
    ;;
  *) usage ;;
esac
