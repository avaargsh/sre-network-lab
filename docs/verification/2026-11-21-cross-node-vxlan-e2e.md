# LAB-03A · Calico 跨节点 VXLAN 双端取证验收（2026-11-21）

## 本轮结论

**PASS：在一次性 Kind + Calico 环境中，客户端与服务端位于不同 Worker，双端 hostNetwork 抓包证实同一 HTTP 请求经 VXLAN UDP/4789 封装传输。**

这不是“PMTU 黑洞修复”或“生产物理网络排障”的证据；LAB-03B 尚未完成。

| 项目 | 独立核验结果 |
| --- | --- |
| 仓库 / Issue | [sre-network-lab](https://github.com/avaargsh/sre-network-lab) / [#6](https://github.com/avaargsh/sre-network-lab/issues/6) |
| 实测代码 | [83811acf](https://github.com/avaargsh/sre-network-lab/commit/83811acfcc0c0fd388a81125f561c558d3d5fb84) |
| 首次 E2E Run | [44861376690](https://github.com/avaargsh/sre-network-lab/actions/runs/44861376690) · SUCCESS |
| 最新文档提交对应 E2E | [44861443136](https://github.com/avaargsh/sre-network-lab/actions/runs/44861443136) · 真实 LAB-03 步骤 SUCCESS |
| 最新证据 Artifact | [calico-policy-evidence](https://github.com/avaargsh/sre-network-lab/actions/runs/44861443136/artifacts/14690030919) |
| Kubernetes / 网络 | Kind 3 Nodes，Kubernetes Server v1.37.0；Calico v3.33.1 |
| Calico IPPool | 192.168.0.0/16；vxlanMode=Always；ipipMode=Never |
| HTTP proof | cross-node-44861443136-1 |

## 双节点实际拓扑（第二次 Run）

| 对象 | 节点 | 实测地址 |
| --- | --- | --- |
| Client / sniffer-src | sre-policy-lab-worker | NodeIP 172.18.0.3；PodIP 192.168.151.198 |
| Server / sniffer-dst | sre-policy-lab-worker2 | NodeIP 172.18.0.2；PodIP 192.168.136.133 |
| Service | ClusterIP | 10.96.193.223 |

- 源 Pod 与目的 Pod 的实际 nodeName 不相同，且与指定的两个 Ready Worker 一致。
- Baseline 与 Recheck 的 PodIP:5678、ClusterIP:80 请求返回相同的正确业务正文。
- 两个 sniffer 分别在源/目标节点的 eth0 抓取来自 172.18.0.3、指向 172.18.0.2:4789 的流量。

## 解码后的同一 HTTP 流证据

两侧抓包均可见如下组合，**来自同一 VXLAN 包而非跨报文拼接**：

~~~text
Outer UDP: 172.18.0.3:ephemeral → 172.18.0.2:4789
VXLAN: flags [I], VNI 4096
Inner TCP: 192.168.151.198:ephemeral → 192.168.136.133:5678
HTTP: GET /?proof=cross-node-44861443136-1 HTTP/1.1
~~~

- 两侧各 **90 个匹配 VXLAN 报文**，各包含 **15 次带正确 proof token 的 HTTP 请求**。
- 两份 PCAP 各 13,749 字节；解码文本与 HTTP 请求证据保存在 Artifact。
- tcpdump 中出现的 checksum offload 警告不是应用数据损坏的独立证据，不作为故障结论。

## 证据完整性

从 GitHub Actions Artifact 下载 ZIP 后独立重新计算哈希：

| 部分 | 阶段数 | 结果 | SHA-256 校验 |
| --- | ---: | --- | ---: |
| LAB-03 跨节点 VXLAN | baseline/capture/recheck（3） | 3/3 PASS | 15/15 匹配 |
| LAB-04 策略回归 | baseline/ingress/egress/additive/recovery（5） | 5/5 PASS | 132/132 匹配 |
| 合计 | 8 | 8/8 PASS | **147/147 匹配** |

SHA-256 文件验证完整性不证明数据来源的独立可信度，因此仍以 Run/Commit、Kubernetes API 快照和原始 PCAP 共同判断。

## 安全与未验收能力

本次 sniffer 使用 hostNetwork + NET_RAW/NET_ADMIN，仅在一次性 Kind 测试集群运行。没有在生产节点或真实物理交换机上注入网络故障。

**不应宣称**：VM MTU/PMTU 黑洞、大包被丢弃后 ICMP 返回、物理 Underlay、BGP、RoCE 或生产网络的真实恢复已完成。LAB-03B 需进一步提供独立 VM 环境的大小包差异、两端抓包、受控注入、回滚及重复请求。

## 面试可用表达

“我在 Kubernetes Kind 的两个 Worker 上固定部署客户端和服务器，在 Calico VXLAN Always 模式下同时抓取两侧节点的 UDP 4789 报文，并在同一包中核对外层 Node IP、内层 PodIP 和唯一 HTTP 请求标记。请求前后都验证 PodIP 与 ClusterIP 正常；将 PCAP、拓扑、请求与哈希校验保存在 GitHub Actions Artifact。这证明了 Kind 中的跨节点封装路径，后续 VM PMTU 故障仍属于独立验证目标。”
