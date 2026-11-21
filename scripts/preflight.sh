#!/usr/bin/env bash
# preflight.sh - 实验环境预检查
set -euo pipefail

echo "=== SRE Network Lab Preflight ==="

command -v kubectl >/dev/null || { echo "kubectl not found"; exit 1; }
command -v kind >/dev/null || echo "kind not found (optional for VM environments)"

echo "--- Cluster Info ---"
kubectl cluster-info
kubectl get nodes -o wide

echo "--- CNI Pods ---"
kubectl -n kube-system get pods -o wide | grep -E 'calico|cilium|flannel|weave|kube-proxy' || true

echo "--- kube-proxy Mode ---"
kubectl -n kube-system get configmap kube-proxy -o yaml 2>/dev/null | grep -E 'mode:|proxy-mode' || echo "kube-proxy ConfigMap not found or mode not set"

echo "--- CoreDNS ---"
kubectl -n kube-system get pods -l k8s-app=kube-dns -o wide || true

echo "--- Kernel & OS (from a node) ---"
NODE=$(kubectl get nodes -o jsonpath='{.items[0].metadata.name}')
kubectl debug node/"$NODE" -it --image=nicolaka/netshoot -- chroot /host uname -a || echo "Node debug failed (may need privileges)"

echo "=== Preflight done ==="
