# VM 环境说明

用于复现复杂 Underlay、MTU、BGP 等 Kind 难以模拟的场景。

建议：

- 至少两台 Linux VM（内核 ≥5.13 以便测试 nftables）
- 安装相同版本的 Kubernetes 与 CNI
- 记录节点间网络拓扑与防火墙规则

具体安装步骤根据发行版自行补充。

## LAB-03B 前置演练

可在一台专用 Linux VM 上执行 [Linux netns PMTU 故障实验](../../scenarios/mtu/pmtu.md)。它不会修改 VM 的真实网卡、默认路由或宿主机防火墙，不能当作两台 VM 的 Underlay 验收。
