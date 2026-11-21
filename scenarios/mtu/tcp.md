# LAB-03C · TCP PMTU 黑洞、重传与 MSS 缓解

本实验复用 LAB-03B 的三个**隔离 Linux Network Namespace**，从 ICMP 探测扩展到真实 TCP/HTTP 数据面。不是独立双 VM 的真实 Underlay。

## 四阶段

| 阶段 | MTU | ICMP Frag Needed | MSS Clamp | HTTP | 双端 PCAP |
| --- | --- | --- | --- | --- | --- |
| baseline | 1500 | 放行 | 无 | 64KiB POST 成功 | Router h0/h1 均见 >1200 字节 TCP payload |
| blackhole | 1200 | DROP | 无 | /health 成功，大 POST 超时 | h0 超限大段与重复 Seq；h1 无超限大段 |
| mitigated | 1200 | DROP | SYN 双向 MSS=1160 | 64KiB POST 成功 | SYNACK MSS≤1160、h1 数据段≤1160 |
| recovery | 1500 | 放行 | 已删除 | 64KiB POST 成功 | Router 两端大 TCP 数据段恢复 |

依赖 iproute2、iptables、tcpdump、ethtool 与 Python 标准库。为使 veth 抓包的 TCP 包大小可解释，仅在临时 Namespace 内关闭 GRO/GSO/TSO，**不触碰宿主机设备**。源 Namespace 内禁用 tcp_mtu_probing，以确保实验的“ICMP 被阻断”能稳定复现，不能据此宣称所有生产系统都会永久卡死。

## 独立 VM 中运行

~~~bash
export RUN_ID="tcp-pmtu-$(date -u +%Y%m%dT%H%M%SZ)"
export EVIDENCE_DIR=/tmp/sre-pmtu-tcp
sudo env SRE_LAB_ALLOW_MUTATIONS=1 SRE_LAB_SCOPE=dedicated-netns \
  RUN_ID="$RUN_ID" EVIDENCE_DIR="$EVIDENCE_DIR" \
  python3 scripts/lab_pmtu_tcp.py
sudo python3 scripts/verify_pmtu_tcp_evidence.py "$EVIDENCE_DIR/$RUN_ID"
~~~

需要 root 和显式双重授权。同名 Namespace 已存在则拒绝覆盖；结束后回收服务器进程与全部自建 Namespace，资源残留必然失败。

两侧 Router 接口产生真实 PCAP、TCP 解码、HTTP JSON 结果、status.json 和 SHA256SUMS；独立校验器重新检查 TCP 重传、MSS 与恢复结果，不能只读取自述 PASS 标签。

**未验证**：独立多 VM Underlay、物理交换机、Calico Overlay 真实 PMTU 故障、生产路由器修改 MSS。多 VM 与 TCP 真实业务拓扑仍由 Issue #6 单独验收。
