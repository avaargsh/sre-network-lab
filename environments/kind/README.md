# Kind 双 Worker 环境示例

```yaml
# cluster.yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
name: sre-network-lab
nodes:
- role: control-plane
- role: worker
- role: worker
```

创建：

```bash
kind create cluster --config cluster.yaml
```

建议安装 Calico 或 Cilium 作为 CNI，并记录版本到证据目录。
