# 第 1 天｜Linux 网络基础：namespace、veth、路由、ARP/邻居、conntrack

## 今天要回答的面试问题

“两个 Pod 都处于 Running，为什么连不上？一个 TCP 包从容器出来，到宿主机和另一容器中究竟经过什么？”

**目标**：区别网络命名空间隔离、L2 邻居发现、L3 路由、连接跟踪与 NAT；不要把它们混为一个功能。

## 1. 模型与最小路径

~~~text
进程 socket
  → namespace A: 路由查找 / neigh(ARP) → eth0
  → veth A ↔ veth peer（宿主机 / bridge / CNI）
  → 宿主机的路由、策略和（可能的）Netfilter
  → veth peer → namespace B → socket
~~~

- **network namespace**：隔离网卡、地址、路由表、网络相关的内核状态；进程共享宿主机内核，但网络视图独立。
- **veth**：成对出现的虚拟以太网接口；一端收包，另一端可收到对应报文。常把一端放 Pod netns、另一端留在宿主机。
- **路由**：先查目标 IP 应该走哪个出接口、下一跳；`ip route get` 是宿主机或当前 namespace 的路由判断，不能代表其他 namespace。
- **ARP / neighbour**：IPv4 在链路上解析下一跳 MAC；IPv6 使用 NDP。访问不同子网的目标时，ARP 通常解析网关/下一跳，不是远端 Pod 的 MAC。
- **conntrack**：Netfilter 的连接状态跟踪，为有状态规则和 NAT 提供映射。**直连 Pod IP 并不意味着必定发生 NAT**；看到 ESTABLISHED 不代表应用层成功。
- **MTU**：网络接口允许的最大 L3 报文大小；Overlay 封装增加额外头部，路径 MTU 与接口 MTU 可能不同。

## 2. 对照表：这个故障该看哪个概念？

| 现象 | 第一证据点 | 不要立刻推断 |
| --- | --- | --- |
| IP 未分配 / 接口 DOWN | `ip -n <ns> addr`、`ip link` | 不是 DNS 问题 |
| `Network is unreachable` | `ip route get <ip>` | 不代表链路丢包 |
| 同网段目标偶发解析失败 | `ip neigh show`、ARP 抓包 | 不是直接证明 CNI Bug |
| SYN 发出但无 SYN-ACK | 源/宿主机/目标多点抓包 | 可能是 ACL、路由或监听问题 |
| Service 访问异常但 Pod IP 正常 | Service 规则 / conntrack | 不能直接怪 Pod 网络 |

## 3. 可复现最小实验（只在隔离 Linux VM / 测试机执行）

已实现脚本入口，在仓库根目录运行：

~~~bash
sudo scripts/lab-netns.sh up
sudo scripts/lab-netns.sh verify
sudo scripts/lab-netns.sh down
~~~

脚本仅创建两端分别位于不同 namespace 的**直连 veth 对**，不经过 Linux bridge、Service DNAT 或跨节点 CNI。它适合验证独立网络栈、直连路由与 ARP；不代表 Kubernetes 实际 Pod 路径。需要 CAP_NET_ADMIN 与 iproute2/ping，已存在同名 Namespace 时脚本会拒绝覆盖。

以下操作使用 root 权限修改**实验主机**网络命名空间，勿在生产节点执行。要求安装 `iproute2`、`ping`。名称须不与现有资源冲突。

~~~bash
sudo ip netns add sre-lab-a
sudo ip netns add sre-lab-b
sudo ip link add sre-veth-a type veth peer name sre-veth-b
sudo ip link set sre-veth-a netns sre-lab-a
sudo ip link set sre-veth-b netns sre-lab-b

sudo ip -n sre-lab-a link set sre-veth-a name eth0
sudo ip -n sre-lab-b link set sre-veth-b name eth0
sudo ip -n sre-lab-a addr add 10.210.10.1/24 dev eth0
sudo ip -n sre-lab-b addr add 10.210.10.2/24 dev eth0
sudo ip -n sre-lab-a link set lo up
sudo ip -n sre-lab-b link set lo up
sudo ip -n sre-lab-a link set eth0 up
sudo ip -n sre-lab-b link set eth0 up

sudo ip netns exec sre-lab-a ip route get 10.210.10.2
sudo ip netns exec sre-lab-a ping -c 3 10.210.10.2
sudo ip netns exec sre-lab-a ip neigh show
sudo ip netns exec sre-lab-b ip -br addr

# 清理（完成实验后执行，任一条失败应检查残留）
sudo ip netns del sre-lab-a
sudo ip netns del sre-lab-b
~~~

**预期**：ping 成功，路由是直连网段，邻居表能观察到对端 MAC；**不应**推导出“发生了 DNAT”。如果失败，先检查接口是否 UP、IP 是否落在同一子网，再看路由与邻居。

## 4. 只读排查命令

~~~bash
ip -br addr; ip route; ip rule
ip route get <DESTINATION_IP>
ip neigh show
ss -tanp
sudo conntrack -L -p tcp 2>/dev/null | head  # 需 conntrack 命令和权限
sudo tcpdump -ni <INTERFACE> 'arp or icmp or tcp'
~~~

抓包点要覆盖 **发出端、节点出口、目标节点入口、目标 Pod** 中至少两个。各点使用同一请求时间窗/5 元组，否则无法证明包在哪里丢失。

## 5. 面试口述（90 秒提纲）

“我先确认源和目标所在 namespace 的 IP、路由和监听状态；然后区分同节点 veth 转发与跨节点 CNI 路由。若路由可达但没有响应，我在源、节点出口和目标抓同一个 5 元组，用 ARP/邻居表和 TCP SYN/SYN-ACK 对比定位丢失位置。若涉及 ClusterIP，另外检查 Service DNAT 与 conntrack。最终以恢复前后同样的请求验证，而不是只看 Pod Running。”

**今日验收**：画出两个 namespace 的直连路径；解释 ARP 解析的是谁；回答“conntrack 与 NAT 是不是一回事”。

参考：[Linux ip-netns](https://man7.org/linux/man-pages/man8/ip-netns.8.html) · [veth](https://man7.org/linux/man-pages/man4/veth.4.html) · [ip-route](https://man7.org/linux/man-pages/man8/ip-route.8.html)
