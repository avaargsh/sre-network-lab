# SRE Network Lab

Kubernetes 网络故障诊断与**证据化排查实验室**，面向 SRE / 平台工程师的复习、面试口述和安全实验。目标不是背组件名称，而是能画出数据包路径、证明丢包在哪一层，并验证故障恢复。

## 7 天系统学习路线（先学模型，再做实验）

| 天数 | 核心主题 | 需要掌握的判断 |
| --- | --- | --- |
| [Day 1](docs/learning-path/day-01-linux-network.md) | Linux namespace / veth / route / ARP / conntrack | 包从哪个接口走？有没有 NAT？ |
| [Day 2](docs/learning-path/day-02-kubernetes-service.md) | DNS / EndpointSlice / DNAT / Service Proxy | PodIP 通但 ClusterIP 不通怎么办？ |
| [Day 3](docs/learning-path/day-03-calico.md) | Calico IPPool / BGP / VXLAN / MTU | 跨节点流量在哪一段消失？ |
| [Day 4](docs/learning-path/day-04-cilium.md) | Cilium eBPF Hook / LB / Hubble / Policy | 为什么抓包不一定看到 VIP？ |
| [Day 5](docs/learning-path/day-05-envoy.md) | Listener / Route / Cluster / xDS | Envoy 503 是哪一层的问题？ |
| [Day 6](docs/learning-path/day-06-istio.md) | Istio mTLS / AuthZ / Sidecar / Ambient | 是身份、TLS 握手还是 L7 拒绝？ |
| [Day 7](docs/learning-path/day-07-incident-drill.md) | 六种故障演练与恢复证据 | 能否形成真实可复述的 RCA？ |

[学习导航与统一验收标准](docs/learning-path/README.md)

## 目录

~~~text
environments/kind/   # 双 Worker Kind 配置（cluster.yaml）
environments/vm/     # Underlay、BGP、MTU 的独立 VM 实验说明
scenarios/           # dns / service / mtu / network-policy / envoy-503 / istio-mtls
scripts/             # lab-netns / lab-dns / lab-service / preflight / collect-evidence / verify-recovery
evidence/            # 报告模板与本地运行证据
docs/learning-path/  # Day 1–7 自学手册
~~~

## 快速开始：Kind 基础实验

~~~bash
git clone https://github.com/avaargsh/sre-network-lab.git
cd sre-network-lab
kind create cluster --config environments/kind/cluster.yaml
kubectl config current-context   # 应为 kind-sre-network-lab
kubectl get nodes -o wide        # 确认所有节点 Ready
bash scripts/preflight.sh  # 可选：会创建 node debug 临时 Pod 并尝试交互
~~~

## 已实现的实验入口（仅个人隔离环境）

**Day 1：Network Namespace + veth**（在专用 Linux VM 或授权测试宿主机执行，需要 CAP_NET_ADMIN）：

~~~bash
sudo scripts/lab-netns.sh up
sudo scripts/lab-netns.sh verify
sudo scripts/lab-netns.sh down
~~~

**LAB-01：客户端 DNS 故障**（不会修改 CoreDNS）：

~~~bash
export SRE_LAB_ALLOW_MUTATIONS=1  # 确认当前为独立测试集群后再设置
scripts/lab-dns.sh setup
scripts/lab-dns.sh baseline
scripts/lab-dns.sh inject
scripts/lab-dns.sh fault
scripts/lab-dns.sh restore
scripts/lab-dns.sh verify
scripts/lab-dns.sh cleanup
~~~

[LAB-01 完整复现与恢复](scenarios/dns/README.md)。

**LAB-02：Service targetPort 故障**（使用独立测试 Kubernetes 集群）：

~~~bash
export SRE_LAB_ALLOW_MUTATIONS=1  # 仅在确认当前连接为隔离测试集群后启用
scripts/lab-service.sh setup
scripts/lab-service.sh baseline
scripts/lab-service.sh inject
scripts/lab-service.sh fault
scripts/lab-service.sh restore
scripts/lab-service.sh verify
scripts/lab-service.sh cleanup  # 删除独立实验工作负载，保留 namespace
~~~

[详细实验步骤与风险边界](scenarios/service/README.md)。除 LAB-01 / LAB-02 外，其他四个故障场景当前仍主要是设计说明，尚无完整自动注入与验收。Kind 默认 CNI 不强制执行 NetworkPolicy，Calico、Cilium、Istio 应在独立适配环境安装。

### 证据采集（路径修复与失败记录）

证据固定写入仓库根目录 `evidence/<RUN_ID>/`（可设置 `EVIDENCE_DIR` 指向其他私人路径），**不再依赖当前工作目录**。baseline 可以自动创建 RUN_ID，但建议显式设置，fault/recovery 必须使用相同 RUN_ID：

~~~bash
export SRE_LAB_ALLOW_MUTATIONS=1  # 仅在确认隔离测试集群后
export RUN_ID="dns-$(date -u +%Y%m%dT%H%M%SZ)"
scripts/lab-dns.sh setup
scripts/collect-evidence.sh dns baseline  # 自动验证 PodIP/VIP/FQDN
scripts/lab-dns.sh inject
scripts/collect-evidence.sh dns fault     # 自动判断 DNS 失败 vs 网络不通
scripts/lab-dns.sh restore
scripts/verify-recovery.sh dns
scripts/collect-evidence.sh dns recovery
scripts/lab-dns.sh cleanup
~~~

已在 `dns` 和 `service` 场景实现主动请求/恢复检查；其他场景返回 `NOT_VERIFIED` 与非零退出。每阶段 `lab-probe.out/.err` 保存请求输出，`phase-status.json` 标明 PASS/FAILED，`command-status.tsv` 记录各命令退出码，`SHA256SUMS` 用于文件完整性核对。若探测失败，采集器会非零退出。真实抓包、CNI/代理模式与根因仍需按 [报告模板](evidence/report-template.md) 补齐，缺失标为 NOT VERIFIED。

## 本地自动化检查（不依赖集群）

~~~bash
bash -n scripts/*.sh
python3 -m unittest discover -s tests -v
~~~

GitHub Actions CI 只运行上述静态/模拟测试，**不是 Kind 或真实 Kubernetes E2E**。

## 隔离 Kind 端到端验证

除了离线 mock 契约测试，现提供 [Kind E2E 工作流](docs/kind-integration.md)，在 GitHub-hosted 的一次性 Kind 集群中运行 LAB-01/02 的 baseline → fault → recovery。该工作流与离线 CI 分开，结果必须以具体 Actions run 的结论和 `evidence` 附件为准，**不能把未运行或失败的工作流写成 E3 已验证**。

## 已验证的 Kind 实验

- [2026-11-21 Kind E2E 验收记录](docs/verification/2026-11-21-kind-e2e.md)：LAB-01 / LAB-02 六个阶段 PASS，证据与限制独立记录。

## LAB-04：Calico NetworkPolicy（独立测试集群）

[实验脚本与五阶段策略矩阵](scenarios/network-policy/README.md) · [专用 Calico Kind 安装](environments/kind/calico-policy.md) · [真实 Calico CI Workflow](.github/workflows/calico-policy-e2e.yml)。

与 LAB-01/02 的默认 Kind 环境不同：LAB-04 使用 **disableDefaultCNI: true**，安装 Calico v3.33.1 并核验 NetworkPolicy **真实执行**。LAB-04 已在三节点 Kind + Calico v3.33.1 下完成真实五阶段验收（应用 Pod 全部位于同一 Worker）；详见 [2026-11-21 验收报告](docs/verification/2026-11-21-calico-policy-e2e.md)。**这不代表跨节点 VXLAN / 物理 MTU 已验证。**

## LAB-03A：跨节点 Calico VXLAN 实验

[详细实验流程与验收范围](scenarios/mtu/README.md) · [实现代码](scripts/lab_cross_node.py)。

已完成真实 Kind + Calico 双节点 VXLAN 验收，见 [2026-11-21 验收报告](docs/verification/2026-11-21-cross-node-vxlan-e2e.md)：源/目的 Worker 不同，双节点 PCAP 关联唯一 HTTP proof token，Baseline/Capture/Recheck 均通过。**VM MTU/PMTU 故障注入仍待开发**，不能用 Kind 的数据代替物理网络故障证据。

## LAB-03B · Linux PMTU 故障演练

[四阶段实验与安全说明](scenarios/mtu/pmtu.md) · [实验控制器](scripts/lab_pmtu.py) · [独立证据校验器](scripts/verify_pmtu_evidence.py)。

在一次性 Linux VM/Runner 中使用三组独立 Network Namespace 复现 MTU 缩小、ICMP Fragmentation Needed 丢失、大包超时与恢复。**这不是独立双 VM Underlay 的真实验收**。是否通过以最新 [PMTU Kernel E2E 工作流](.github/workflows/pmtu-netns-e2e.yml) 和证据为准。

## 六个场景

| 场景 | 内容 | 最低可接受证据 |
| --- | --- | --- |
| [LAB-01 DNS](scenarios/dns/) | 解析异常 | dig/nslookup、CoreDNS/策略信息、恢复 |
| [LAB-02 Service](scenarios/service/) | Service 转发异常 | PodIP、VIP、EndpointSlice 对照 |
| [LAB-03 MTU](scenarios/mtu/) | PMTU 黑洞 | 包大小差异与两点抓包 |
| [LAB-04 NetworkPolicy](scenarios/network-policy/) | 网络策略拒绝 | 实际有策略能力的 CNI、拒绝/恢复 |
| [LAB-05 Envoy](scenarios/envoy-503/) | 503 | 响应标志、Route/Cluster/Endpoint |
| [LAB-06 Istio mTLS](scenarios/istio-mtls/) | 认证冲突 | 明文/mTLS 请求矩阵、策略与证书 |

## 安全和可信边界

- **仅在可回收的隔离实验环境执行故障注入**，不要对生产 CoreDNS、CNI、节点 MTU 或 mesh-wide 授权做实验。
- 把“设计方案”“控制面配置”“数据面真实证据”“恢复闭环”分开陈述。
- 公开 evidence 前务必检查日志、地址、令牌及可能包含的敏感配置。
- 所有命令输出随 Kubernetes、Linux 内核、CNI、Service Proxy 版本和模式变化，应记录实际版本。

本仓库目前定位为 **学习材料 + LAB-01/02 已实跑集成测试 + LAB-04 已真实验收 + LAB-03A 跨节点 VXLAN 已真实验收，LAB-03B Linux netns PMTU 演练独立验收**，不是经过真实集群全面验收的网络故障平台。
