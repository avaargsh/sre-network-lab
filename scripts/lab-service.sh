#!/usr/bin/env bash
# Lab-02. Mutates only resources in the dedicated sre-lab-service namespace.
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
NS=sre-lab-service
MANIFEST="$ROOT/scenarios/service/manifests.yaml"
EXPECTED=sre-network-lab-ok
ATTEMPTS=${LAB_ATTEMPTS:-15}
RETRY_DELAY=${LAB_RETRY_DELAY:-2}
[[ "$ATTEMPTS" =~ ^[1-9][0-9]*$ ]] || { echo "Invalid LAB_ATTEMPTS" >&2; exit 2; }

usage() { echo "Usage: $0 {setup|baseline|inject|fault|restore|verify|cleanup}" >&2; exit 2; }
need() { command -v kubectl >/dev/null || { echo 'kubectl is required' >&2; exit 1; }; }
target_port() { kubectl -n "$NS" get svc lab-echo -o jsonpath='{.spec.ports[0].targetPort}'; }
probe_pod() { kubectl -n "$NS" get pod -l app=lab-probe -o jsonpath='{.items[0].metadata.name}'; }
echo_ip() { kubectl -n "$NS" get pod -l app=lab-echo -o jsonpath='{.items[0].status.podIP}'; }
service_ip() { kubectl -n "$NS" get svc lab-echo -o jsonpath='{.spec.clusterIP}'; }
request() {
  local url=$1 output
  output=$(kubectl -n "$NS" exec "$CLIENT" -- curl -fsS --connect-timeout 2 --max-time 5 "$url") || return 1
  [[ "$output" == *"$EXPECTED"* ]]
}
prepare() {
  CLIENT=$(probe_pod)
  POD_IP=$(echo_ip)
  SERVICE_IP=$(service_ip)
  [[ -n "$CLIENT" && -n "$POD_IP" && -n "$SERVICE_IP" && "$SERVICE_IP" != None ]] || { echo 'Probe or backend Pod not ready' >&2; return 1; }
}
check() {
  local want=$1 kind=$2 url=$3 i
  for ((i=1; i<=ATTEMPTS; i++)); do
    if request "$url"; then
      if [[ "$want" == ok ]]; then echo "PASS $kind: success"; return 0; fi
    else
      if [[ "$want" == fail ]]; then echo "PASS $kind: expected failure"; return 0; fi
    fi
    sleep "$RETRY_DELAY"
  done
  echo "FAIL $kind: expected $want did not occur" >&2
  return 1
}

need
case "${1:-}" in
  setup|inject|restore|cleanup)
    [[ "${SRE_LAB_ALLOW_MUTATIONS:-}" == 1 ]] || {
      echo 'Refusing mutation. Review kubectl current-context and set SRE_LAB_ALLOW_MUTATIONS=1 for an isolated test cluster.' >&2
      exit 3
    }
    ;;
esac
case "${1:-}" in
  setup)
    kubectl apply -f "$MANIFEST"
    kubectl -n "$NS" rollout status deployment/lab-echo --timeout=120s
    kubectl -n "$NS" rollout status deployment/lab-probe --timeout=120s
    echo 'Ready: run baseline, then inject/fault, then restore/verify.'
    ;;
  baseline|verify)
    [[ "$(target_port)" == http ]] || { echo 'Service targetPort is not restored to http' >&2; exit 1; }
    prepare
    check ok pod-ip "http://$POD_IP:5678/"
    check ok cluster-ip "http://$SERVICE_IP:80/"
    check ok service-dns 'http://lab-echo.sre-lab-service.svc.cluster.local:80/'
    ;;
  inject)
    [[ "$(target_port)" == http ]] || { echo 'Refusing to inject: targetPort is not http' >&2; exit 1; }
    kubectl -n "$NS" patch svc lab-echo --type=json -p='[{"op":"replace","path":"/spec/ports/0/targetPort","value":5679}]'
    echo 'Fault injected: Service targetPort=5679 while backend listens on 5678.'
    ;;
  fault)
    [[ "$(target_port)" == 5679 ]] || { echo 'Fault not installed; targetPort != 5679' >&2; exit 1; }
    prepare
    check ok pod-ip "http://$POD_IP:5678/"
    check fail cluster-ip "http://$SERVICE_IP:80/"
    check fail service-dns 'http://lab-echo.sre-lab-service.svc.cluster.local:80/'
    kubectl -n "$NS" get endpointslices -l kubernetes.io/service-name=lab-echo -o wide
    ;;
  restore)
    [[ "$(target_port)" == 5679 ]] || { echo 'Refusing to restore: targetPort != 5679' >&2; exit 1; }
    kubectl -n "$NS" patch svc lab-echo --type=json -p='[{"op":"replace","path":"/spec/ports/0/targetPort","value":"http"}]'
    echo 'Restored targetPort=http. Run verify.'
    ;;
  cleanup)
    # Do NOT delete the namespace: it may contain unrelated user resources.
    kubectl -n "$NS" delete deployment/lab-echo deployment/lab-probe svc/lab-echo --ignore-not-found=true
    echo "Dedicated lab workloads deleted; namespace $NS retained."
    ;;
  *) usage ;;
esac
