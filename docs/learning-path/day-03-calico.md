# 第 3 天｜Calico：IPPool、BGP/VXLAN、MTU 与 NetworkPolicy

## 1. 首先确认安装模式，不要凭经验猜默认值

Calico 支持纯路由、BGP、IPIP、VXLAN 和 eBPF 等不同方案。重点对象：

- **IPPool**：CIDR、`ipipMode`、`vxlanMode`、NAT 出口等；从现存配置判断而不是背“默认 BGP”。
- **Felix**：将策略、路由相关意图与节点数据面配置协调起来，具体规则实现与所选数据面有关。
- **BGP（适用时）**：负责向其他节点/路由器传播到 Pod CIDR 的路由；BGP Established 不足以证明每条流量都通。
- **VXLAN/IPIP**：跨节点封装；外层地址是节点 Underlay IP，内层地址是 Pod IP。封装增加开销，需计算 MTU。
- **NetworkPolicy**：依赖实际 CNI 强制执行；策略存在不等于已生效，入口、出口分别判断。

## 2. 把路径与故障模式对应起来

| 模式 | 发包节点关键阶段 | 重点检查 |
| --- | --- | --- |
| 无封装路由 | Pod → 宿主机路由 → Underlay | PodCIDR 路由传播、交换机/云网络可达 |
| IPIP | 内层 Pod IP 包封装为 IP-in-IP | 协议 4、通路 ACL、接口 MTU |
| VXLAN | 内层帧封装为 UDP VXLAN | UDP 4789、VTEP、路径 MTU |
| eBPF 数据面 | BPF 策略和转发程序 | 实际启用状态、map/事件，不只看 iptables |

**陷阱**：宿主机之间 ping 通，不代表跨节点 PodIP 能通；可能是 Overlay UDP/协议 4 被阻止，也可能是 PodCIDR 路由缺失。

## 3. 只读检查表

~~~bash
kubectl -n kube-system get pods -o wide | grep -E 'calico|tigera'
kubectl get ippools.crd.projectcalico.org -o yaml
kubectl get networkpolicy -A
ip route; ip -d link
ip route get <REMOTE_POD_IP>
# 仅当实际启用 BGP 且 calicoctl 可用时
sudo calicoctl node status
# 用已确认的宿主机接口抓包；需要权限
sudo tcpdump -ni <UNDERLAY_IF> 'udp port 4789 or proto 4'
~~~

针对同一测试请求，至少记录发送节点和接收节点的外层包。若看到发端有 VXLAN 而收端没有，继续调查 Underlay；若接收端有外层包却没有交付 Pod，调查解封装、路由和策略。

## 4. MTU 练习与风险界限

- 先记录 `ip link show`、Pod 接口 MTU 与宿主 Underlay MTU，计算封装开销（注意 IPv4/IPv6 和实现差异）。
- 对相同目标进行大小包对照；`ping -M do` 只适用于支持 DF 的 IPv4 测试环境，不要将 ICMP 成功等同于 TCP 正常。
- **不要在生产集群直接修改物理网卡、VXLAN 接口或节点 MTU**。需要真正 PMTU 黑洞时使用可清理的 VM 拓扑。
- 对疑似安全策略拒绝，用有能力的 CNI 观测验证，不能简单以未出现 deny 日志判断“无拦截”。

配套：[LAB-03 MTU](../../scenarios/mtu/) · [LAB-04 NetworkPolicy](../../scenarios/network-policy/) · [Calico 专用 Kind 环境](../../environments/kind/calico-policy.md)。

LAB-04 实验重点是 Ingress、Egress 的分别隔离和多策略叠加。应从实际发起请求的客户端观察成功/失败，不能用“NetworkPolicy YAML 已创建”宣称策略生效；默认 kindnet 无此强制能力。

### LAB-03 跨节点报文证据

[LAB-03A](../../scenarios/mtu/) 的脚本固定不同 Worker，再从两侧节点 Underlay 抓包，关联内层 PodIP、外层节点 IP 和唯一 HTTP proof token。仅有 IPPool 配置或仅抓到任意 UDP 4789，都不足以独立证明具体请求走了跨节点 VXLAN。

VM PMTU 黑洞仍待单独故障注入与恢复验收。可先使用 [LAB-03B 真实 Linux netns PMTU 实验](../../scenarios/mtu/pmtu.md)，通过 `ping -M probe` 的大小包对照、两侧抓包、ICMP Type 3 Code 4 和恢复矩阵理解机制。

## 5. 90 秒口述模板

“我先确认 Calico IPPool 和数据面模式，再根据流量路径拆成 Pod→Node、节点间 Underlay、隧道解封装和目标 Pod 四段。跨节点异常优先查看 PodCIDR 路由/BGP 或 VXLAN/IPIP 通道，并同步抓内外层报文、核对 MTU。如果仅有特定源或目的失败，再验证策略命中。恢复以后同时做小包、大包和 TCP 请求对照。”

参考：[Calico 官方文档](https://docs.tigera.io/calico/latest/networking/) · [Calico 源码](https://github.com/projectcalico/calico)
