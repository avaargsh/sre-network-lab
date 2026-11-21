# SRE Network Lab

Kubernetes 网络故障诊断与证据化排查实验室。

面向 SRE / Kubernetes 平台工程师的可复现实验集合，覆盖 DNS、Service 转发、跨节点 MTU、NetworkPolicy、Envoy 503、Istio mTLS 等典型故障场景。

## 目标

- 每个实验都能画出数据包路径
- 收集故障前 / 故障中 / 恢复后的证据
- 用自己的语言解释根因
- 形成可在面试中复述的案例

## 目录结构

```
sre-network-lab/
├── README.md
├── environments/          # 实验环境定义
│   ├── kind/
│   └── vm/
├── scenarios/             # 故障场景
│   ├── dns/
│   ├── service/
│   ├── mtu/
│   ├── network-policy/
│   ├── envoy-503/
│   └── istio-mtls/
├── scripts/               # 通用脚本
│   ├── preflight.sh
│   ├── collect-evidence.sh
│   └── verify-recovery.sh
└── evidence/              # 每次运行的证据（按 run-id 存放）
    └── <run-id>/
        ├── environment.json
        ├── baseline/
        ├── fault/
        ├── recovery/
        └── report.md
```

## 快速开始

```bash
# 1. 选择环境（Kind 双节点示例）
cd environments/kind
kind create cluster --config cluster.yaml

# 2. 执行预检查
../../scripts/preflight.sh

# 3. 进入某个场景
cd ../../scenarios/dns
cat README.md   # 查看注入步骤与验收标准

# 4. 收集证据（示例）
../../scripts/collect-evidence.sh dns baseline
# ... 注入故障 ...
../../scripts/collect-evidence.sh dns fault
# ... 恢复 ...
../../scripts/collect-evidence.sh dns recovery
../../scripts/verify-recovery.sh dns
```

## 实验清单

| 实验 | 场景 | 优先级 | 验收重点 |
|------|------|--------|----------|
| LAB-01 | DNS | P0 | CoreDNS 日志、解析失败与恢复 |
| LAB-02 | Service | P0 | EndpointSlice、DNAT、conntrack |
| LAB-03 | MTU | P0 | 不同包大小、PMTU 黑洞、抓包 |
| LAB-04 | NetworkPolicy | P0 | 双向隔离、策略叠加、拒绝证据 |
| LAB-05 | Envoy 503 | P1 | Route/Cluster、NR/UH/UF 标志 |
| LAB-06 | Istio mTLS | P1 | PeerAuthentication、握手结果 |

## 证据标准

每次运行必须包含：

- `environment.json`：Kubernetes、CNI、内核、kube-proxy 模式版本
- `baseline/`、`fault/`、`recovery/`：对应阶段的命令输出与抓包
- `report.md`：现象 → 假设 → 证据 → 根因 → 恢复验证

## 注意事项

- 仅在隔离实验环境执行故障注入
- 脚本应支持幂等与清理
- 记录实际使用的版本，避免与生产差异导致结论偏差

## 相关知识体系

参见配套复习笔记（SRE 网络工程复习体系 V3.0）：

- Linux 网络 + Pod/Service 路径
- Calico / Cilium 数据面差异
- Envoy / Istio 配置与实际请求处理
- 证据化排查顺序：应用 → DNS → Service → Pod 网络 → 策略 → 代理
