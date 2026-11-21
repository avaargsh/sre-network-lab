#!/usr/bin/env bash
# Fail closed: a successful exit must mean an actual defined check passed.
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
SCENARIO=${1:-}
case "$SCENARIO" in
  dns)
    exec "$ROOT/scripts/lab-dns.sh" verify
    ;;
  service)
    exec "$ROOT/scripts/lab-service.sh" verify
    ;;
  network-policy)
    exec "$ROOT/scripts/lab-policy.sh" verify
    ;;
  mtu|envoy-503|istio-mtls)
    echo "NOT_VERIFIED: no automated recovery assertion is implemented for $SCENARIO" >&2
    exit 3
    ;;
  *)
    echo "Usage: $0 <service|dns|mtu|network-policy|envoy-503|istio-mtls>" >&2
    exit 2
    ;;
esac
