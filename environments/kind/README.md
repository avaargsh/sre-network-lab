# Kind 两 Worker 基础环境

本目录现在包含 [cluster.yaml](cluster.yaml)，可创建 **1 Control Plane + 2 Worker** 的基础 Kind 集群：

~~~bash
kind create cluster --config environments/kind/cluster.yaml
kubectl config current-context  # 预期 kind-sre-network-lab
kubectl get nodes -o wide       # 预期 3 个节点，须等待 Ready
~~~

**非常重要**：

- 默认配置保留 Kind 自带 CNI（kindnet），可用于 Linux 路由、DNS、Service 基础学习，但**不具备 NetworkPolicy 强制执行能力**。
- LAB-04 必须使用支持并实际启用 NetworkPolicy 的 Calico 或 Cilium 集群，并用拒绝请求验证策略确实被执行。
- 若要改用 Calico/Cilium，请单独建立集群，在创建时按对应版本官方文档配置 `disableDefaultCNI`，然后安装单一 CNI；Cilium kube-proxy replacement 还需另外处理 kube-proxy 与内核/cgroup 前提。
- Kind 节点是容器，不是完整物理 Underlay；需要真正重现 BGP/PMTU 时使用 `environments/vm`。
- 不建议直接在已有生产节点使用故障注入示例。

学习顺序参见 [7 天复习路线](../../docs/learning-path/README.md)。
