# LAB-04 Calico NetworkPolicy · 真实 Kind 集成验收（2026-11-21）

## 证据元数据

| 项目 | 记录 |
| --- | --- |
| Commit | [9137bddc](https://github.com/avaargsh/sre-network-lab/commit/9137bddc36dfe687bdb6695ee3e1700c3ff1e30e) |
| Actions Run | [44860303252](https://github.com/avaargsh/sre-network-lab/actions/runs/44860303252) |
| Artifact | [calico-policy-evidence](https://github.com/avaargsh/sre-network-lab/actions/runs/44860303252/artifacts/14689771772) |
| 运行结论 | completed / success |
| 环境 | GitHub-hosted Ubuntu + 3-node Kind，Kubernetes v1.37.0 |
| Linux kernel | 7.0.0-1020-azure（CI 节点，不代表生产内核） |
| CNI | Calico v3.33.1，Tigera Operator；原生 projectcalico.org/v3 IPPool |
| Calico dataplane | Installation 显示 Iptables，IPPool 配置 VXLAN Always、IPIP Never |
| Pod CIDR | 192.168.0.0/16 |
| Run ID | policy-44860303252-1 |

## 五阶段 HTTP 请求矩阵

两个独立 Probe Pod 分别访问同一 Server 的 PodIP:5678 与 ClusterIP:80。服务器返回固定标记 `sre-network-lab-policy-ok`。

| 阶段 | Allowed PodIP | Blocked PodIP | Allowed VIP | Blocked VIP |
| --- | --- | --- | --- | --- |
| baseline | PASS | PASS | PASS | PASS |
| ingress | PASS | **拒绝 curl 28** | PASS | **拒绝 curl 28** |
| egress | PASS | **拒绝 curl 28** | PASS | **拒绝 curl 28** |
| additive | PASS | PASS | PASS | PASS |
| recovery | PASS | PASS | PASS | PASS |

- **Ingress**：服务器侧 default deny + 仅允许带 allowed 标签来源的入站策略；受限客户端无法访问。
- **Egress**：撤销服务器侧策略后，受限客户端侧 default deny egress；受限客户端无法访问。
- **Additive**：服务器 default deny + 两条独立 ingress allow 规则，Allowed 与 Blocked 均可访问；说明允许策略叠加。
- **Recovery**：清理具名实验策略，再验证双方恢复。

这些请求没有依赖 DNS；拒绝必须由 curl 7/28 连续两次确认，本次实测均为 28（超时）。

## 审计证据完整性

从 CI 上传的 ZIP 中独立读取并核验：

- 五份 `phase-status.json`：全部 `probe_exit_code=0`、`probe_status=PASS`；
- 五份 `lab-probe.out`：共 20 条 PASS 行，均与预期矩阵一致；
- 五份 `command-status.tsv`：每份 12 条，合计 60 条均为退出码 0；
- 五份 `SHA256SUMS`：共 132 项文件校验，**0 个 hash mismatch**；
- `baseline/calico-ippools.out`：IPPool 的 `vxlanMode=Always`、`ipipMode=Never`、CIDR 192.168.0.0/16，`Allocatable=True`；
- `baseline/calico-operator.out`：Calico Iptables 数据面 + VXLAN 配置。
- 证据保存在 Actions Artifact（配置保留 7 天），未把原始运行日志直接提交到 Git 仓库。

## 故障恢复与 CI 自身的经验

初版 CI 在关闭 Kind 默认 CNI 后仍要求创建时等待 Node Ready，造成安装 Calico 前超时风险；修复为创建阶段不等待 Node Ready，安装 Calico 后明确等待。

随后真实 CI 发现 Operator 异步协调：提交 Installation CR 后 `calico-system` Namespace 尚未创建。修复为等待 Namespace 和 calico-node DaemonSet 出现，再检查 DaemonSet Rollout 与节点 Ready。

另外，Calico v3.33 新安装采用原生 `projectcalico.org/v3` IPPool；证据采集和 CI 命令由旧 API 组修订为原生组。以上修复全部包含在验证提交中。

## 明确不支持的宣称

本次三份实验业务 Pod（server、allowed client、blocked client）**全部调度于同一个 Kind Worker 节点**。虽然 Calico IPPool 配置显示 VXLAN Always，但这次请求路径没有证明跨节点 VXLAN 实际传包。

本次已证实的是 **同节点 Calico NetworkPolicy 执行、策略叠加、PodIP/ClusterIP 请求对照以及撤销后的恢复**。

尚未验收：跨节点 CNI 数据包路径、VXLAN 抓包、MTU/PMTU 黑洞、物理 Underlay 或 BGP、Calico 内核逐包 DROP 的独立证据。需要 LAB-03 或后续专门的跨节点 E2E 补足。

## 面试可用的准确表达

“我在独立的三节点 Kind + Calico 3.33 集群中设计了五阶段 NetworkPolicy 实验。通过两个独立客户端向 PodIP 和 ClusterIP 发起真实 HTTP 请求，确认入口与出口方向的拒绝行为、多份允许策略叠加，以及撤销策略后的恢复。实验同时保存了策略对象、Calico IPPool、请求退出码和五阶段证据哈希，并在 GitHub Actions 中完整复现。当前验证只覆盖同节点策略，不把它包装为物理跨节点网络排障成果。”
