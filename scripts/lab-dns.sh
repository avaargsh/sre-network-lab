#!/usr/bin/env bash
# LAB-01: only the dedicated test client's Pod DNS configuration is modified.
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "$0")/.." && pwd)
NS=sre-lab-dns
DEPLOY=lab-dns-probe
MANIFEST="$ROOT/scenarios/dns/manifests.yaml"
EXPECTED=sre-network-lab-dns-ok
FQDN=lab-dns-echo.sre-lab-dns.svc.cluster.local
ATTEMPTS=$(printenv LAB_ATTEMPTS || printf '12')
DELAY=$(printenv LAB_RETRY_DELAY || printf '2')
[[ "$ATTEMPTS" =~ ^[1-9][0-9]*$ && "$DELAY" =~ ^[0-9]+$ ]] || { echo 'Invalid retry configuration' >&2; exit 2; }

usage() { echo "Usage: $0 {setup|baseline|inject|fault|restore|verify|cleanup}" >&2; exit 2; }
need() { command -v kubectl >/dev/null || { echo 'kubectl required' >&2; exit 1; }; }
dns_policy() { kubectl -n "$NS" get deployment "$DEPLOY" -o jsonpath='{.spec.template.spec.dnsPolicy}'; }
probe_pod() { kubectl -n "$NS" get pod -l app=lab-dns-probe -o jsonpath='{.items[0].metadata.name}'; }
backend_ip() { kubectl -n "$NS" get pod -l app=lab-dns-echo -o jsonpath='{.items[0].status.podIP}'; }
service_ip() { kubectl -n "$NS" get svc lab-dns-echo -o jsonpath='{.spec.clusterIP}'; }
prepare() {
  CLIENT=$(probe_pod); POD_IP=$(backend_ip); SERVICE_IP=$(service_ip)
  [[ -n "$CLIENT" && -n "$POD_IP" && -n "$SERVICE_IP" && "$SERVICE_IP" != None ]] || {
    echo 'Probe Pod, backend Pod, or ClusterIP missing' >&2; exit 1;
  }
}
# Verify DNS curl fails specifically with curl exit 6, not arbitrary network errors.
attempt() {
  local name=$1 expected=$2 rc=0 output='' i accepted=0
  shift 2
  for ((i=1; i<=ATTEMPTS; i++)); do
    if output=$(kubectl -n "$NS" exec "$CLIENT" -- "$@" 2>&1); then rc=0; else rc=$?; fi
    printf 'PROBE=%s ATTEMPT=%d RC=%d EXPECTED=%s OUTPUT=%q\n' "$name" "$i" "$rc" "$expected" "$output"
    if [[ "$expected" == ok && "$rc" -eq 0 ]]; then
      if [[ "$name" != *curl* || "$output" == *"$EXPECTED"* ]]; then accepted=1; break; fi
    elif [[ "$expected" == dns-fail ]]; then
      if [[ "$name" == nslookup && "$rc" -ne 0 ]]; then accepted=1; break; fi
      if [[ "$name" == dns-curl && "$rc" -eq 6 ]]; then accepted=1; break; fi
    fi
    sleep "$DELAY"
  done
  ((accepted == 1)) || { echo "FAIL $name expected $expected" >&2; return 1; }
  echo "PASS $name expected $expected"
}
check() {
  local mode=$1
  prepare
  attempt pod-curl ok curl --noproxy '*' -fsS --connect-timeout 2 --max-time 5 "http://$POD_IP:5678/"
  attempt vip-curl ok curl --noproxy '*' -fsS --connect-timeout 2 --max-time 5 "http://$SERVICE_IP:80/"
  if [[ "$mode" == fault ]]; then
    attempt nslookup dns-fail nslookup -timeout=1 -retry=1 "$FQDN"
    attempt dns-curl dns-fail curl --noproxy '*' -fsS --connect-timeout 2 --max-time 5 "http://$FQDN:80/"
  else
    attempt nslookup ok nslookup -timeout=1 -retry=1 "$FQDN"
    attempt dns-curl ok curl --noproxy '*' -fsS --connect-timeout 2 --max-time 5 "http://$FQDN:80/"
  fi
}
(( $# == 1 )) || usage
need
case "$1" in
  setup|inject|restore|cleanup)
    [[ "$(printenv SRE_LAB_ALLOW_MUTATIONS || true)" == 1 ]] || {
      echo 'Refusing mutation: check kubectl current-context and set SRE_LAB_ALLOW_MUTATIONS=1 only for an isolated test cluster' >&2
      exit 3
    }
    ;;
esac
case "$1" in
  setup)
    kubectl apply -f "$MANIFEST"
    kubectl -n "$NS" rollout status deployment/lab-dns-echo --timeout=120s
    kubectl -n "$NS" rollout status deployment/"$DEPLOY" --timeout=120s
    ;;
  baseline|verify)
    [[ "$(dns_policy)" == ClusterFirst ]] || { echo 'Client DNS policy is not ClusterFirst' >&2; exit 1; }
    check baseline
    ;;
  inject)
    [[ "$(dns_policy)" == ClusterFirst ]] || { echo 'Refusing inject; client DNS policy is not ClusterFirst' >&2; exit 1; }
    kubectl -n "$NS" patch deployment "$DEPLOY" --type=json -p='[{"op":"replace","path":"/spec/template/spec/dnsPolicy","value":"None"},{"op":"add","path":"/spec/template/spec/dnsConfig","value":{"nameservers":["127.0.0.1"],"options":[{"name":"timeout","value":"1"},{"name":"attempts","value":"1"}]}}]'
    kubectl -n "$NS" rollout status deployment/"$DEPLOY" --timeout=120s
    echo 'Fault injected: DNS client now uses 127.0.0.1'
    ;;
  fault)
    [[ "$(dns_policy)" == None ]] || { echo 'Fault not installed; DNS policy is not None' >&2; exit 1; }
    check fault
    ;;
  restore)
    [[ "$(dns_policy)" == None ]] || { echo 'Refusing restore; client DNS policy is not None' >&2; exit 1; }
    kubectl -n "$NS" patch deployment "$DEPLOY" --type=json -p='[{"op":"remove","path":"/spec/template/spec/dnsConfig"},{"op":"replace","path":"/spec/template/spec/dnsPolicy","value":"ClusterFirst"}]'
    kubectl -n "$NS" rollout status deployment/"$DEPLOY" --timeout=120s
    echo 'Restored ClusterFirst'
    ;;
  cleanup)
    kubectl -n "$NS" delete deployment/lab-dns-echo deployment/"$DEPLOY" svc/lab-dns-echo --ignore-not-found=true
    echo "Removed dedicated workloads; namespace $NS retained"
    ;;
  *) usage ;;
esac
