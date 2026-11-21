# LAB-03B：真实 Linux PMTU 黑洞与恢复演练

本阶段在**一台专用 Linux VM 或 GitHub-hosted 一次性 Runner** 内创建三个独立 network namespace，真实使用 Linux 内核路由/转发、MTU、iptables 和 tcpdump，复现 ICMP Fragmentation Needed 被丢弃造成的 PMTU 黑洞。

**边界：这是 Linux 内核 netns 故障实验，不是独立双 VM、物理 Underlay、BGP 或 RoCE 的 E2E 证据。**

## 固定拓扑

~~~text
src(10.207.10.2/24)  →  router(h0=10.207.10.1/24, h1=10.207.20.1/24)  →  dst(10.207.20.2/24)
eth0 MTU 1500                h0 MTU 1500 / h1 1500→1200→1500                eth0 MTU 1500
~~~

| 阶段 | Router 出口 MTU | ICMP Frag Needed | 小包 | DF 大包 | 证据 |
|---|---:|---|---|---|---|
| baseline | 1500 | 放行 | — | 成功 | router 两侧均见大包 |
| blackhole | 1200 | **仅 Router netns OUTPUT 定向丢弃** | 成功 | **超时** | h0 见大包，h1 只见小包 |
| discovery | 1200 | 恢复放行 | — | 不可转发，收到 ICMP | h0 捕获 Router→源端 Type 3 Code 4、MTU 1200；h1 空 PCAP 是合理现象 |
| recovery | 1500 | 放行 | — | 成功 | Router 两侧均见大包 |

大包载荷 1400 字节（IPv4 总长度 1428），小包载荷 900 字节（IPv4 总长度 928）。使用 `ping -M probe` 强制发送 DF 报文并避开历史 PMTU 缓存的影响。**Ping 检测不等于已经验证 TCP MSS、TCP 重传或真实 HTTP 恢复。**

## 在专用 VM 运行

脚本只允许操作 `sre-pmtu-src`、`sre-pmtu-hop`、`sre-pmtu-dst` 三个新建 Namespace，所有 MTU、路由、转发与 iptables 修改限制在 Namespace 内，不更改宿主机真实网络接口或宿主机防火墙；已有同名 Namespace 则拒绝覆盖。必须使用 root/CAP_NET_ADMIN，且显式双重授权。

~~~bash
# 仅在独立、允许回收的测试 VM 上执行
sudo apt-get install -y iproute2 iptables iputils-ping tcpdump
export RUN_ID="pmtu-$(date -u +%Y%m%dT%H%M%SZ)"
export EVIDENCE_DIR="/tmp/sre-pmtu-evidence"

sudo env SRE_LAB_ALLOW_MUTATIONS=1 SRE_LAB_SCOPE=dedicated-netns \
  RUN_ID="$RUN_ID" EVIDENCE_DIR="$EVIDENCE_DIR" \
  python3 scripts/lab_pmtu.py

python3 scripts/verify_pmtu_evidence.py "$EVIDENCE_DIR/$RUN_ID"
~~~

脚本是**一次性执行四阶段的原子实验**，不暴露单独的无回滚注入命令。正常结束和失败时都会尝试删除它创建的 Namespace；清理有残留则退出失败。禁用 `SRE_LAB_ALLOW_MUTATIONS` 时拒绝运行。

## 独立证据验收

运行后证据位于 `EVIDENCE_DIR/RUN_ID`，每阶段有真实 Ping 标准输出/退出码、router h0/h1 的 PCAP、解码文本、`status.json` 和 `SHA256SUMS`；根目录有环境内核版本、终止状态与清理结果。`verify_pmtu_evidence.py` 必须重新判断包大小、真实 ICMP 错误、前后 MTU 路径以及文件哈希是否一致。

**注意**：discovery 时路由出口可能只产生 24 字节的 PCAP 文件头（0 个匹配包），这是“大包没有通过出口”的有效负证据；只有在确实包含入口大包和返回的 Type 3 Code 4 的情况下才能接受，不可泛化为空抓包即 PASS。

GitHub Actions 的 [Linux PMTU 实测工作流](../../.github/workflows/pmtu-netns-e2e.yml) 在一次性 Ubuntu Runner 中以独立 Namespace 运行；只有 CI 成功且原始证据经过审计，才能将此阶段标记为 **真实 Linux 内核实验通过**。

## 后续真实双 VM 与业务验收

`Issue #6` 后续还需两个独立 VM + 受控 Router/Underlay：三台 VM 的网络拓扑/路由记录、双点抓包、小包/大包/TCP 业务矩阵、可回滚 MTU/ACL 注入、ICMP 返回和恢复后业务实测。**当前代码尚不能证明上述真实多 VM 路径。**
