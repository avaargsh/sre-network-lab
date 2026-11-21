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
scripts/             # lab-netns / lab-service / preflight / collect-evidence / verify-recovery
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

[详细实验步骤与风险边界](scenarios/service/README.md)。除 LAB-02 外，其他五个故障场景当前仍主要是设计说明，尚无完整自动注入与验收。Kind 默认 CNI 不强制执行 NetworkPolicy，Calico、Cilium、Istio 应在独立适配环境安装。

### 证据采集（路径修复与失败记录）

证据固定写入仓库根目录 `evidence/<RUN_ID>/`（可设置 `EVIDENCE_DIR` 指向其他私人路径），**不再依赖当前工作目录**。baseline 可以自动创建 RUN_ID，但建议显式设置，fault/recovery 必须使用相同 RUN_ID：

~~~bash
export RUN_ID="dns-$(date -u +%Y%m%dT%H%M%SZ)"
scripts/collect-evidence.sh dns baseline
# 仅在隔离实验环境注入并实际复现后，再采集 fault
scripts/collect-evidence.sh dns fault
# 恢复并手动验证请求，再采集 recovery
scripts/collect-evidence.sh dns recovery
# dns 等非 Service 场景尚未实现自动验收，非零退出为预期
scripts/verify-recovery.sh dns
~~~

只在 `service` 场景实现主动恢复检查；未实现的场景返回 `NOT_VERIFIED` 与非零退出。每阶段的单项采集错误写入 `command-status.tsv`，完整性需要人工核查。真实抓包、CNI/代理模式与根因仍需按 [报告模板](evidence/report-template.md) 补齐，缺失标为 NOT VERIFIED。

## 本地自动化检查（不依赖集群）

~~~bash
bash -n scripts/*.sh
python3 -m unittest discover -s tests -v
~~~

GitHub Actions CI 只运行上述静态/模拟测试，**不是 Kind 或真实 Kubernetes E2E**。

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

本仓库目前定位为 **学习材料 + 前两个实验的可执行脚本 + 离线契约测试**，不是经过真实集群全面验收的网络故障平台。
