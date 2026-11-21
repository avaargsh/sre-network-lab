# LAB-03C · TCP PMTU 黑洞、重传与 MSS 规则的真实验收（2026-11-21）

## 结论边界

在 GitHub-hosted Linux Runner 的**单个内核、三个隔离 Network Namespace** 中，完成了端到端真实 64KiB HTTP POST 的正常、故障、缓解和恢复四阶段；同时从模拟路由器 Namespace 的 h0/h1 两个接口保存 TCP PCAP 和解码证据。

这是**Linux TCP 栈和受控路由 Namespace 的真实行为验证**，不是物理机、独立双 VM、Kubernetes CNI、云网络或生产路由设备的已验证经验。

| 证据 | 结果 |
| --- | --- |
| 已运行的可执行代码 | [9cb3b0df](https://github.com/avaargsh/sre-network-lab/commit/9cb3b0dfdc0b952e5d31b1584a54333e5df452b1) |
| GitHub Actions | [Run 44863953699](https://github.com/avaargsh/sre-network-lab/actions/runs/44863953699) · completed/success |
| 原始 Artifact | [tcp-pmtu-mss-evidence](https://github.com/avaargsh/sre-network-lab/actions/runs/44863953699/artifacts/14691330691) |
| Linux Kernel | 7.0.0-1020-azure |
| RUN_ID | tcp-pmtu-44863953699-1 |
| 作用域 | src → router(h0/h1) → dst，均为 Linux Netns，源端 tcp_mtu_probing=0 |
| 最终状态 | result=PASS；namespaces_remaining=[] |

## 四阶段 HTTP 与 TCP 数据面矩阵

| 阶段 | Router 出口 MTU | ICMP Frag Needed | MSS 策略 | HTTP 实测 | h0 最大 TCP payload | h1 最大 TCP payload | 重传 |
| --- | ---: | --- | --- | --- | ---: | ---: | ---: |
| baseline | 1500 | 放行 | 无 | 64KiB POST，200 + SHA256 内容匹配 | 1448 | 1448 | 0 |
| blackhole | 1200 | Router OUTPUT 内 DROP | 无 | GET /health 200；大 POST 超时 | 1448 | 140 | **5** |
| mitigated | 1200 | 仍 DROP | FORWARD 双向 TCPMSS 1160 | GET /health 200；64KiB POST 200 | 1148 | 1148 | 0 |
| recovery | 1500 | 恢复放行 | 规则已撤销 | 64KiB POST 200 | 1448 | 1448 | 0 |

- **黑洞并非 DNS 或 HTTP 5xx**：Router 出口仍然转发小 TCP 包，GET health 正常；64KiB 大请求在入口多次重传，却无法以原尺寸从出口转发，客户端因超时失败。
- **MSS 解决的是本实验中的 TCP 数据段大小问题**：实际 SYN-ACK 抓包 MSS 从 1460 变成 **1160**，数据段 TCP payload 上限为 **1148**；即使仍阻断 ICMP Frag Needed，大 POST 也成功。
- **恢复**：删除临时 MSS 规则和 ICMP DROP、将路由出口 MTU 恢复到 1500，重新见到双端 1448 字节大数据段与 HTTP 成功。

## 证据完整性

已下载 GitHub Artifact ZIP 并在独立工具进程中重新计算：四阶段各 8 项、根目录 3 项，**总计 35/35 SHA-256 匹配**。四份 status.json 均为 PASS，四组真实 TCP PCAP 均包含可解码报文。独立 `verify_pmtu_tcp_evidence.py` 对两侧 TCP seq、MSS、最大 payload、HTTP 返回结果、Namespace 清理做了重复判定。

SHA-256 表示同一证据包内的文件一致性，并不能提供第三方签名证明。Artifact 保留期限由工作流配置决定（当前 7 天）；长期文档存放源提交与 Run 链接。

## 首轮 CI 失败与修复记录

1. TDD 测试模块使用文件路径导入实验控制器时，未把 scripts 目录加入 Python 导入路径；修复后离线回归通过。
2. 初版 GitHub workflow 将 `${{ github.run_id }}` 多保留了一层反斜杠，安全门禁拒绝非法 RUN_ID；修复为原生 GitHub Actions 表达式。
3. 首次真实 HTTP 请求成功，但立即停止 tcpdump，内核缓冲区未排空而生成 24 字节空 PCAP；开启 tcpdump immediate-mode 并等待缓冲区排空后，PCAP 及数据面断言通过。**不能用成功 HTTP 请求替代成功抓包**。

## 生产排障表达

“我设计了一个隔离 Linux Namespace 路由实验，主动把路径 MTU 从 1500 收紧为 1200，再阻断 ICMP Fragmentation Needed。观察到 HTTP 健康检查正常但 64KiB POST 超时，源侧有 5 次 TCP 重传，Router 出口没有超限数据段。增加只作用于实验 Router 的 MSS=1160 规则后，业务恢复且 PCAP 中 SYN-ACK MSS 和 TCP payload 同时降低；删除规则、恢复 MTU 后大 TCP 数据段再次通过。所有阶段保存 PCAP、HTTP/日志和哈希，并独立校验。”

**不能宣称**已解决真实两台 VM 的 MTU 故障、Calico 数据面 PMTU 生产问题或 RoCE/物理 Underlay 问题；这些仍须通过 Issue #6 后续实测。
