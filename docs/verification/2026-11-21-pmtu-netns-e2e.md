# LAB-03B Phase 1 · Linux PMTU 黑洞真实验收（2026-11-21）

## 已验证结论

**已验证：在一个隔离 Linux Runner 内，真实创建 src/router/dst 三个 network namespace，复现路径 MTU 变小、ICMP Fragmentation Needed 被阻断导致大包黑洞、恢复 ICMP 后的 MTU 反馈，以及 MTU 复原后的大包通信恢复。**

**尚未验证：两台/三台独立 VM、真实物理 Underlay、TCP MSS/重传、跨节点 Calico 的 PMTU 黑洞。** 本实验是 Issue #6 的 LAB-03B 第一阶段。

| 项目 | 证据 |
|---|---|
| 已验收代码 | [038a088b](https://github.com/avaargsh/sre-network-lab/commit/038a088b7fefcf5ac7d2c4a9400756cdb4971a18) |
| Linux E2E | [Actions Run 44862955886](https://github.com/avaargsh/sre-network-lab/actions/runs/44862955886) — SUCCESS |
| Artifact | [kernel-pmtu-evidence](https://github.com/avaargsh/sre-network-lab/actions/runs/44862955886/artifacts/14690323602) |
| Kernel | 7.0.0-1019-azure（一次性 GitHub-hosted Runner） |
| Run ID | pmtu-44862955886-1 |
| 作用域 | 三个专用 network namespaces，非真实双 VM |
| 清理结论 | result=PASS，namespaces_remaining=[] |

## 实际拓扑

~~~text
src 10.207.10.2 ── h0 router 10.207.10.1 ── h1 router 10.207.20.1 ── dst 10.207.20.2
source eth0 1500       router h0 1500      router h1 1500 → 1200 → 1500       dest eth0 1500
~~~

四个阶段均用 `ping -M probe` 显式 DF 探测，避免缓存 PMTU 影响。大包使用 ICMP payload 1400（IP 总长 1428 字节），小包为 900（IP 总长 928 字节）。

| 阶段 | 大包发送/接收 | 观察证据 | 结果 |
| --- | --- | --- | --- |
| baseline | 2/2 | Router h0/h1 均抓到 1428 字节 Echo Request | PASS |
| blackhole | 0/2（100% 丢失） | h0 看到 1428 字节大包，h1 只看到 928 字节小包；ICMP Frag Needed 被 router namespace 内的 OUTPUT 规则丢弃 | PASS |
| discovery | 0/2，但返回 ICMP 错误 | Router h0 抓到 10.207.10.1 → 10.207.10.2 的 `unreachable - need to frag (mtu 1200)`；h1 PCAP 只有 24 字节文件头，符合无出口转发 | PASS |
| recovery | 2/2 | 恢复 h1 MTU 1500 后，h0/h1 均重新抓到大包 | PASS |

## 可信证据审计

从真实 CI Artifact 独立检查：

- 四阶段 `status.json` 均为 PASS，并重新检查各 Ping 退出码、标准输出和 Router h0/h1 的真实 tcpdump 解码。
- 黑洞阶段小包 2/2 成功，大包 0/2，阻断场景下没有 Frag Needed 反馈；恢复 ICMP 后能看到来自路由节点的真实 Type 3 Code 4。
- 四阶段共 **33 项文件 SHA-256** 匹配；根目录 2 项单独校验，合计 **35/35** 匹配。
- `result.json` 明确记录 `namespaces_remaining=[]`，无遗留网络命名空间。
- GitHub Actions 中独立 `verify_pmtu_evidence.py` 检查成功，不依赖脚本自身 PASS 标签作为唯一判断。

## 失败与修复记录

1. 首次 CI 使用 `sudo -E`，Runner 没有保留授权环境变量，安全门禁按预期拒绝操作；改为固定变量的 `sudo env ...`。
2. discovery 阶段 Router 出口没有符合过滤器的 ICMP 包，tcpdump 生成 24 字节空 PCAP。这是大包不被转发的负证据，不能当作损坏文件；仅在 discovery/egress 条件下允许，并独立核对入口大包与返回的 Fragmentation Needed。
3. 实验生成的目录为 root 私有，非 root 独立校验器无权读取；以只读 sudo 执行验证，仍保留私有权限。

以上修改后重新运行，真实四阶段故障实验及独立证据门禁通过。

## 面试可用表述与未完成项

“我用独立 Linux network namespace 设计了三段路由拓扑，在 Router 出口将 MTU 从 1500 降到 1200，并仅在 Router Namespace 内阻断 ICMP Fragmentation Needed。通过 DF 大小包对照和 Router 双接口抓包确认了大包黑洞；恢复 ICMP 时能看到 MTU 1200 的 Fragmentation Needed；恢复 MTU 后大包再次通过。全过程通过独立证据审计与 GitHub CI 验收。”

**不可说**已完成真实双 VM Underlay 的 PMTU 故障、TCP MSS/重传治理或 Calico 生产 MTU 修复；这几项仍需要单独集成与复盘证据。
