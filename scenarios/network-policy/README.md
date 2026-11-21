# LAB-04 · Calico NetworkPolicy 真实执行矩阵

本实验验证 Ingress / Egress 分别隔离、多个允许策略的**叠加**、以及撤销策略后的恢复。验证基于真实 HTTP 请求，而不是只检查 NetworkPolicy 对象。

环境要求：**独立的 Kind + Calico**（不能使用默认 kindnet）、kubectl、jq、两个可拉取的容器镜像（hashicorp/http-echo:1.0.0、nicolaka/netshoot:v0.14）。完整安装步骤见 [Calico Kind 环境](../../environments/kind/calico-policy.md)。

## 五阶段矩阵

| 阶段 | Allowed 客户端 PodIP / VIP | Blocked 客户端 PodIP / VIP | 策略 |
| --- | --- | --- | --- |
| baseline | PASS / PASS | PASS / PASS | 无 NetworkPolicy |
| ingress | PASS / PASS | **拒绝 / 拒绝** | 服务器默认拒绝入站 + 只允许 Allowed 来源 |
| egress | PASS / PASS | **拒绝 / 拒绝** | 只拒绝 Blocked 客户端全部出站 |
| additive | PASS / PASS | PASS / PASS | 服务器默认拒绝 + 两条独立来源允许策略叠加 |
| recovery | PASS / PASS | PASS / PASS | 删除本实验策略 |

所有 HTTP 请求使用直接 PodIP / ClusterIP，避免 DNS 被 egress 策略拒绝时误判。策略拒绝只接受 curl 退出码 **7 或 28 且连续两次出现**；不接受 DNS 错误码 6、HTTP 错误或 kubectl 失败。脚本会记录真实请求输出供复盘。

## 复现实验

以下命令**只允许**在隔离 Kind 集群中运行。改动只涉及专用 Namespace：sre-lab-policy，脚本不会删除整个 Namespace，也不会修改 kube-system。

~~~bash
kubectl config current-context  # 应为 kind-sre-policy-lab
export SRE_LAB_ALLOW_MUTATIONS=1
export SRE_LAB_EXPECT_CONTEXT=kind-sre-policy-lab
export RUN_ID="policy-$(date -u +%Y%m%dT%H%M%SZ)"

scripts/lab-policy.sh setup
scripts/collect-evidence.sh network-policy baseline

scripts/lab-policy.sh inject-ingress
scripts/collect-evidence.sh network-policy ingress

scripts/lab-policy.sh inject-egress
scripts/collect-evidence.sh network-policy egress

scripts/lab-policy.sh inject-additive
scripts/collect-evidence.sh network-policy additive

scripts/lab-policy.sh restore
scripts/verify-recovery.sh network-policy
scripts/collect-evidence.sh network-policy recovery
scripts/lab-policy.sh cleanup
~~~

**安全门禁**：修改 Kubernetes 对象需要 SRE_LAB_ALLOW_MUTATIONS=1，且当前 kubeconfig context 必须与 SRE_LAB_EXPECT_CONTEXT 一致（默认为 kind-sre-policy-lab）。启动前验证确实装有 Calico Operator 与 calico-node。未知的 NetworkPolicy 会阻止 restore，以免误删非实验对象。

## 验收证据

每阶段由 collect-evidence.sh 记录 lab-probe.out/.err、phase-status.json、command-status.tsv、SHA256SUMS、Calico IPPool/Installation 和策略快照。验收需检查真实请求、策略状态、集群版本和哈希一致性；单纯的 CI mock 不等于数据面验收。Calico 未必默认提供逐包 deny 日志，不能把日志缺失当成放行的证据。

本实验并不证明真实物理网络中的 VXLAN、BGP 或 MTU 行为，Kind 底层为 Docker 网络。未来 LAB-03 需要另外增加真实 Underlay 和多点抓包。

参考：[Kubernetes NetworkPolicy](https://kubernetes.io/docs/concepts/services-networking/network-policies/) · [Calico Kind 安装](https://docs.tigera.io/calico/latest/getting-started/kubernetes/kind)。
