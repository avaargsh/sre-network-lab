# LAB-03A：跨 Worker Calico VXLAN 报文取证

当前目标：证实客户端 Pod 与服务端 Pod **确实位于不同 Kind Worker**，并在两个 Worker 的 Underlay eth0 抓到与同一 HTTP 请求标记关联的 VXLAN 数据包。

这不同于 LAB-03B 的 VM MTU/PMTU 黑洞故障，不能混为一项验收。

## 真实数据路径

~~~text
Kind Worker A                             Kind Worker B
  lab03-client [PodIP A]                    lab03-server [PodIP B]:5678
    ↓                                          ↑
  Calico vxlan.calico  → VXLAN/UDP 4789 →  Calico vxlan.calico
    ↓                                          ↑
  eth0 (hostNetwork sniff-src)              eth0 (hostNetwork sniff-dst)
~~~

脚本从 Kubernetes Node API 选择两个不同的 Ready Worker，显式将 client/server/sniffer 的 `nodeName` 分配到指定 Worker，再检查实际 `spec.nodeName`。不能把默认调度下的同节点通信冒充跨节点测试。

## 安全要求

- 仅运行于新建的 Calico Kind 测试集群，默认 kubectl context 为 `kind-sre-policy-lab`。
- `setup`、`cleanup` 必须设置 `SRE_LAB_ALLOW_MUTATIONS=1` 且 kube context 与 `SRE_LAB_EXPECT_CONTEXT` 一致。
- 抓包 Pod 运行在 hostNetwork，具有 NET_RAW/NET_ADMIN 能力，允许查看节点网络；**禁止直接部署到生产节点**。
- 源、目的两个节点的 eth0 抓包仅过滤 src/dst 节点 IP 与 UDP 4789。请求使用同一个 run-id 写入 URL 的 `?proof=` 参数。
- 捕获文件可能包含网络数据和地址，公开前脱敏；本仓库的 gitignore 不提交运行证据。

## 命令（独立 Kind + Calico 环境）

先按 [Calico Kind 搭建步骤](../../environments/kind/calico-policy.md) 完成安装，确认 Calico IPPool 的 vxlanMode=Always 与 ipipMode=Never，然后在仓库根目录运行：

~~~bash
kubectl config current-context  # 应为 kind-sre-policy-lab
export SRE_LAB_ALLOW_MUTATIONS=1
export SRE_LAB_EXPECT_CONTEXT=kind-sre-policy-lab
export RUN_ID="cross-node-$(date -u +%Y%m%dT%H%M%SZ)"

python3 scripts/lab_cross_node.py setup
python3 scripts/lab_cross_node.py baseline
python3 scripts/lab_cross_node.py capture
python3 scripts/lab_cross_node.py recheck
python3 scripts/lab_cross_node.py cleanup
~~~

## 三层验收

| 阶段 | 必需证据 | 判定 |
| --- | --- | --- |
| baseline | Pod 与 Node 映射、Calico IPPool、PodIP / Service VIP HTTP | 不同 Ready Worker，IP/VIP 两种方式成功 |
| capture | 两端 PCAP、解码文本、带唯一 RUN_ID 的 HTTP 请求 | 每端都能找到**同一包内**匹配节点 IP、PodIP:5678、VXLAN、proof token |
| recheck | 对比 topology 与两条 HTTP 请求 | 节点/Pod/IP 不漂移，IP/VIP 继续成功 |

证据默认输出到 `evidence/<RUN_ID>/{baseline,capture,recheck}`，并生成 `status.json` 和 `SHA256SUMS`。

**目前不宣称** VM MTU 黑洞、PMTUD、ICMP Fragmentation Needed、物理交换机或 RoCE 网络已经得到验证。即使 Kind UDP 4789 取证通过，也仅证明 Docker Underlay 中的跨节点封装。

## LAB-03B：后续 VM MTU / PMTU 故障

独立 VM 验收必须补充真实小包/大包差异、DF 与 ICMP 返回、两端抓包、受控故障注入和自动回滚，并在故障恢复后重复业务请求。不能在生产节点直接改变 MTU 或防火墙。

参考：[Calico VXLAN/IPIP](https://docs.tigera.io/calico/latest/networking/configuring/vxlan-ipip) · [Issue #6](https://github.com/avaargsh/sre-network-lab/issues/6)。
