# 7 天 SRE 网络工程训练营

目标：不只背组件名，而是能够画出真实数据包路径、提出可证伪的假设、取得数据面证据，并在 90 秒内复述一个生产故障。

## 使用方式

- 每天建议 60–90 分钟：模型 20 分钟、只读观测 20 分钟、隔离环境动手 20–40 分钟、录音复盘 10 分钟。
- 每天记录 **版本与模式**、**预测包路径**、**实际证据**、**错误假设**、**恢复验收**。
- [第 1 天：Linux 网络基础](day-01-linux-network.md)
- [第 2 天：Kubernetes Service](day-02-kubernetes-service.md)
- [第 3 天：Calico 跨节点](day-03-calico.md)
- [第 4 天：Cilium eBPF](day-04-cilium.md)
- [第 5 天：Envoy 数据面](day-05-envoy.md)
- [第 6 天：Istio 安全与流量](day-06-istio.md)
- [第 7 天：模拟故障演练](day-07-incident-drill.md)

## 实验环境重要前提

- Kind 默认 CNI 是 kindnet，**不能假设 NetworkPolicy 会被强制执行**；Calico/Cilium 练习要安装匹配 CNI，并记录版本及安装模式。
- Calico 与 Cilium 的实验属于不同环境配置，不要求同时安装在同一集群。
- kind 的节点实质是容器；某些 Underlay、BGP、PMTU 和内核级实验需要独立 VM 才能真实复现。
- Sidecar 和 Ambient 是不同的数据面模式，先确认工作负载实际属于哪个模式。
- 默认所有修改命令只用于个人隔离集群；不对生产 CoreDNS、节点 MTU、集群级授权进行故障注入。
- 没有真实报文、配置快照与恢复前后对比，不应声称“故障实证闭环”。

## 学习完成判定（每一天）

1. 手画一张流量图，能标注源/目标 IP、发生 SNAT/DNAT 的位置和抓包点。
2. 执行至少三条只读诊断命令，能解释正常与异常输出的区别。
3. 至少提出两个竞争假设，说明如何排除其中一个。
4. 按「现象 → 分层 → 证据 → 根因 → 恢复」口述 90 秒，避免把推测说成事实。

配套实验：[六个场景](../../scenarios/) · [证据报告模板](../../evidence/report-template.md)
