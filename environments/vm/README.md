## LAB-03D Stage 2B：四接口 TCP 抓包

[运行手册](lab03d.md) 介绍三台独立 VirtualBox VM 上的 src、router 双口与 dst 的四点取证。仅固定合成 TCP 业务，最长 120 秒、每接口最多 512 包。当前只有离线契约测试，没有真实三 VM PCAP 证据。

## LAB-03D · 三 VM Underlay 实验入口

[完整操作手册](lab03d.md) 已包含三台独立 VirtualBox VM、两段 Internal Network、只读跨机路由/HTTP 验收与清理。CI 不具备真实 VirtualBox 三 VM E2E 证据，故障注入阶段尚未完成。

# VM 环境说明

用于复现复杂 Underlay、MTU、BGP 等 Kind 难以模拟的场景。

建议：

- 至少两台 Linux VM（内核 ≥5.13 以便测试 nftables）
- 安装相同版本的 Kubernetes 与 CNI
- 记录节点间网络拓扑与防火墙规则

具体安装步骤根据发行版自行补充。

## LAB-03B 前置演练

可在一台专用 Linux VM 上执行 [Linux netns PMTU 故障实验](../../scenarios/mtu/pmtu.md)。它不会修改 VM 的真实网卡、默认路由或宿主机防火墙，不能当作两台 VM 的 Underlay 验收。
