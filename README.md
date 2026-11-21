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

## LAB-03B · Linux PMTU 黑洞与恢复实测

[四阶段实验](scenarios/mtu/pmtu.md) · [真实 Linux 数据面证据报告](docs/verification/2026-11-21-pmtu-netns-e2e.md) · [受控控制器](scripts/lab_pmtu.py) · [独立校验器](scripts/verify_pmtu_evidence.py)。

已在一次性 Linux Runner 的**三个隔离 network namespace** 完成真实内核 MTU 黑洞、ICMP Fragmentation Needed 与恢复验收，保留双接口 PCAP 和 SHA-256 证据。**尚未完成独立双 VM 的真实 Underlay/TCP 业务验收。**

## LAB-03C · TCP PMTU 黑洞与 MSS 缓解

[操作说明](scenarios/mtu/tcp.md) · [真实 Linux E2E 验收](docs/verification/2026-11-21-tcp-pmtu-mss-e2e.md) · [独立 GitHub Actions](.github/workflows/pmtu-tcp-e2e.yml)。

已在单个隔离 Linux Runner 上，完成真实 64KiB HTTP POST 的正常→黑洞→MSS 缓解→恢复，并通过双端 PCAP 观察 TCP 重传、SYNACK MSS 与数据段大小变化。**未在两台独立 VM 或生产网络验收。**

## LAB-03D Stage 2C：精确 POST 证据与真实三 VM 手动 E2E 入口

[实验验收手册](environments/vm/lab03d.md) · [专用 Runner Workflow](.github/workflows/lab03d-real-vbox.yml)。

四点 PCAP 验证已从相同 TCP SYN + 任意数据，改为**相同 HTTP POST 初始序号 + 四点共享上传数据范围**，防止健康检查流量导致假阳性。新增专用 VirtualBox 自托管 Runner 上手动执行真实三 VM 验收的工作流。**当前尚无真实 VM 执行与 PCAP Artifact。**

## LAB-03D Stage 2B：三 VM 四接口 TCP PCAP 工具（待真实实测）

[操作手册](environments/vm/lab03d.md) · [Guest 抓包](environments/vm/pcap_vm.py) · [Host 协调器](environments/vm/vm_pcap_baseline.py) · [独立证据验收](environments/vm/verify_vm_pcap.py)。

现已实现固定私网接口合成流量的限时 PCAP 抓取及真实 TCP SYN/数据段关联规则；**缺少实际独立 VM 的完整四点抓包实测**，不能把离线通过等同于生产 PMTU 闭环。

## LAB-03D Stage 2A · 90 秒 MTU 租约和自动回滚（待真实三 VM 验收）

[Router watchdog/恢复手册](environments/vm/lab03d.md) · [Root Helper](environments/vm/router_fault.py) · [Host 控制器](environments/vm/vm_mtu_drill.py) · [独立证据校验](environments/vm/verify_vm_fault.py)。

已加入先安装并确认 systemd 定时回滚、再修改实验 Router 私网接口 MTU 的受控逻辑，以及 HTTP 恢复与哈希门禁；**尚无三 VM E2E、PCAP 或跨机黑洞实测。**

## LAB-03D · 三独立 VirtualBox VM Underlay 拓扑门禁（待实测）

[启动与验证手册](environments/vm/lab03d.md) · [Vagrant VM 定义](environments/vm/Vagrantfile) · [跨机验收控制器](environments/vm/lab_vm.py) · [独立证据校验器](environments/vm/verify_vm_evidence.py)。

已实现 src/router/dst 的内部数据网络、机器身份、跨网段路由和真实 64 KiB HTTP 基线的只读验证程序。**GitHub CI 仅运行离线契约测试；实际三 VM 启动、PCAP 和故障注入尚未验收。**

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

本仓库已验证 LAB-01/02、LAB-03A 和 LAB-04 的 Kind 数据面，LAB-03B Linux ICMP PMTU 故障，以及 LAB-03C 真实 TCP/HTTP 黑洞、MSS 缓解和恢复。独立双 VM、Envoy 与 Istio 尚未完成真实 E2E。
