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
scripts/             # preflight / collect-evidence / verify-recovery
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

然后选择 [DNS](scenarios/dns/) 或 [Service](scenarios/service/) 场景阅读对应 README。**目前六个场景主要是实验说明和命令模板，不代表已经具备全部自动注入 YAML 或自动验收。** 默认 Kind CNI 不保证 NetworkPolicy 生效，Calico、Cilium、Istio 需单独搭建适配环境。

### 证据采集（现有脚本行为与限制）

脚本 `collect-evidence.sh` 的证据目录相对于**当前工作目录**（`../../evidence`），所以应从场景目录调用。每次实验固定同一个 RUN_ID：

~~~bash
cd scenarios/dns
export RUN_ID="dns-$(date +%Y%m%d-%H%M%S)"
bash ../../scripts/collect-evidence.sh dns baseline
# 只在隔离实验环境内注入故障，随后采集 fault
bash ../../scripts/collect-evidence.sh dns fault
# 恢复服务并手动验证请求后，采集 recovery
bash ../../scripts/collect-evidence.sh dns recovery
bash ../../scripts/verify-recovery.sh dns
~~~

本仓库的验证脚本对部分场景仍为占位行为。成功退出不等于真实恢复验证通过。抓包、CNI/内核版本、请求结果和根因要按 [报告模板](evidence/report-template.md) 补齐，缺失信息标为 NOT VERIFIED。

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

本仓库目前定位为 **学习材料与实验骨架**，不是已经完成的自动化网络故障平台。
