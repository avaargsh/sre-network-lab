# 第 4 天｜Cilium eBPF：Hook、Service LB、Hubble 与 Policy

## 1. 数据面必须按 Hook 理解

~~~text
Pod 应用 connect() → cgroup/socket-LB（视配置）
    → Pod 网卡 / TC ingress/egress（视配置）
    → Linux 路由 / 节点网络 / 目标端
    → Cilium Endpoint Identity + L3/L4 Policy
    → Hubble 流量事件（取决于启用的监测点）
~~~

- **cgroup socket Hook**：可能在建立连接阶段改写 Service 目标地址；因而某些抓包点看不到原始 ClusterIP。
- **TC Hook**：在网卡收发路径执行 BPF 程序，可做转发、策略和负载均衡逻辑。
- **XDP**：更靠近驱动入口的高性能 Hook，不意味着所有 Cilium Service 请求都经过 XDP。
- **Service LB**：kube-proxy replacement 的具体路径随内核、Helm 配置、Socket LB bypass 等设置变化。
- **Identity + Policy**：通过身份标签/策略判定，而不是只记住 PodIP；Hubble 不等于所有内核包的无损记录。

## 2. Cilium 与传统路径对照

| 排障对象 | kube-proxy + 传统 CNI | Cilium replacement |
| --- | --- | --- |
| Service 数据面 | iptables / nftables / IPVS | BPF maps + BPF programs |
| 生效证据 | 规则、conntrack、抓包 | Cilium 配置、BPF 状态、Hubble、抓包 |
| NetworkPolicy | 看所装 CNI 的策略引擎 | Cilium identity / Policy 与具体 datapath |
| 健康结论 | 对照实际请求 | 对照实际请求（不是仅看 agent Ready） |

## 3. 最小只读演练

先确认当前集群**已安装** Cilium，并且 CLI 与服务端版本兼容：

~~~bash
kubectl -n kube-system get ds cilium -o wide
kubectl -n kube-system get cm cilium-config -o yaml
cilium status
hubble status
hubble observe --since 5m
hubble observe --verdict DROPPED --since 5m
# 在特定 Cilium agent 容器内，命令与路径随版本有所变化
kubectl -n kube-system exec ds/cilium -- cilium-dbg service list
~~~

`cilium connectivity test` 会创建资源、测试流量与策略，只在预留的实验环境/授权窗口执行，不应当当作纯只读诊断。

## 4. 两个可证伪的假设

**情境 A：curl ClusterIP 失败，抓不到 VIP 报文。**
- H1：socket LB 已经选后端，所以抓包点看不到 VIP（并非失败证据）。
- H2：Service 映射或后端存在问题。
- 验证：对比 Service/EndpointSlice、Cilium Service map 与同一请求的真实 PodIP/流量事件。

**情境 B：Hubble 没出现 DROPPED。**
- H1：本次请求没有被该观测点/采样捕捉。
- H2：请求确实没有经过该策略拒绝点。
- 验证：查 CLI/Relay 状态、指定源/目的过滤，再结合抓包和应用返回结果；不能直接断定“策略没问题”。

**90 秒口述**：“我先确认是否启用 kube-proxy replacement、采用哪些 Hook，再用 Service/EndpointSlice 与 BPF 映射互证。Hubble 用于观察源目的身份、判定和丢包原因，必要时结合 tcpdump/pwru 做多点定位。最后以同样请求的成功和拒绝行为验收。”

参考：[Cilium kube-proxy replacement](https://docs.cilium.io/en/stable/network/kubernetes/kubeproxy-free/) · [Hubble](https://github.com/cilium/hubble) · [pwru](https://github.com/cilium/pwru)
