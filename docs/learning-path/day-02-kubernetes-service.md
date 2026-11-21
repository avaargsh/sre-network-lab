# 第 2 天｜Kubernetes Service：DNS、EndpointSlice、DNAT 与代理模式

## 今天要回答的面试问题

“PodIP 能访问，Service ClusterIP 不能访问。你怎么在 5 分钟内把问题缩小到 DNS、后端端点或 Service 数据面？”

## 1. 将控制面与数据面画开

~~~text
客户端进程 →（需要时）CoreDNS 查询 → ClusterIP
                                     │
API: Service + EndpointSlice ──> kube-proxy/CNI 监听 ──> 节点转发规则
                                     │
请求经 iptables/nftables 或 eBPF 选取后端 → PodIP:targetPort → 应用
~~~

- DNS 是**名字到 IP** 的解析环节，不是每个已建立连接都会查 DNS。
- 普通 ClusterIP Service 经虚拟 IP 负载均衡；**Headless Service** 通常直接通过 DNS 提供后端地址，没有同样的 ClusterIP VIP 转发路径。
- EndpointSlice 是后端地址集合，需区分 `ready`、`serving`、`terminating`；零个 Ready 端点与“网络丢包”性质不同。
- kube-proxy 通常**写规则，不逐包代理流量**。iptables/nftables 规则交给内核匹配及 DNAT；Cilium 替代模式可能在 socket/TC 等 Hook 处完成选后端。
- 连接跟踪可保留既有 NAT 映射，因此“改了 EndpointSlice，新连接恢复但老连接没变”不一定矛盾。

## 2. 对比代理实现

| 实现 | 选择后端发生在哪里 | 常用证据 | 易误判之处 |
| --- | --- | --- | --- |
| kube-proxy iptables | Netfilter 规则 | `iptables-save -t nat`、conntrack | kube-proxy 不在用户态逐包处理 |
| kube-proxy nftables | nftables 规则 | `nft list ruleset`、conntrack | NodePort / 防火墙行为与 iptables 不完全相同 |
| kube-proxy IPVS（旧） | IPVS + 部分 iptables | `ipvsadm -Ln` | 已弃用，迁移计划应看版本 |
| Cilium replacement | socket / TC / 其他 eBPF Hook | Cilium BPF Service Map、Hubble | 抓包不一定能见原始 ClusterIP |

不能把“iptables O(n)、eBPF O(1)”当成端到端性能结论；测量新连接 TPS、延迟、CPU 与真实规则规模。

## 3. 诊断实验：同一客户端对比三种目标

已新增 [LAB-02 可执行场景](../../scenarios/service/)：`scripts/lab-service.sh setup / baseline / inject / fault / restore / verify / cleanup`。需要确认 kubectl 当前上下文并显式设置 `SRE_LAB_ALLOW_MUTATIONS=1`；在专用测试集群中将 Service `targetPort` 从 `http` 故意改成 `5679`，让后端 PodIP 正常但 VIP 转发失败，然后恢复并验证。此实验无需断言 kube-proxy 的具体实现，适用于不同数据面模式。

以下使用示例 namespace `demo`、Service `api`，运行前按真实资源替换；不修改集群资源。

~~~bash
kubectl -n demo get svc api -o yaml
kubectl -n demo get endpointslices -l kubernetes.io/service-name=api -o yaml
kubectl -n demo get pods -o wide
kubectl -n kube-system get cm kube-proxy -o yaml 2>/dev/null
# 在已存在且装有 curl/dig 的调试 Pod 内执行：
dig api.demo.svc.cluster.local
curl -sv --connect-timeout 3 http://api.demo.svc.cluster.local
curl -sv --connect-timeout 3 http://<CLUSTER_IP>:<PORT>
curl -sv --connect-timeout 3 http://<BACKEND_POD_IP>:<TARGET_PORT>
~~~

**故障归因顺序**：

1. 域名失败但 ClusterIP 成功 → 首查 DNS、search domain、CoreDNS 与 DNS Service。
2. PodIP 成功但 ClusterIP 失败 → 首查 selector、EndpointSlice、端口映射、Service 数据面。
3. 所有 PodIP 均失败 → 先检查应用监听、NetworkPolicy 和 Pod 网络。
4. 只有部分节点或后端失败 → 检查 `internalTrafficPolicy`、`externalTrafficPolicy`、拓扑及 Ready 条件。

配套：[LAB-01 DNS](../../scenarios/dns/) · [LAB-02 Service](../../scenarios/service/)。

## 4. 证据与口述

保存 Service/EndpointSlice 快照、同一客户端的 DNS/ClusterIP/PodIP 请求结果、模式判定和抓包时间窗。如果没有数据面证据，报告写“怀疑代理规则”而非“DNAT 失败已证实”。

**90 秒回答**：“先将名字解析、服务端点同步与节点实际转发拆开。直连 PodIP 做对照，读 EndpointSlice 的 Ready 状态，然后依据 kube-proxy 或 Cilium 模式检查规则/BPF 映射与连接跟踪；明确观测能排除哪些层，恢复后再次访问域名、VIP 和 PodIP。”

参考：[Service](https://kubernetes.io/docs/concepts/services-networking/service/) · [EndpointSlice](https://kubernetes.io/docs/concepts/services-networking/endpoint-slices/) · [虚拟 IP 与 Service 代理](https://kubernetes.io/docs/reference/networking/virtual-ips/)
