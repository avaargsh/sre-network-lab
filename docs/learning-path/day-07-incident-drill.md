# 第 7 天｜综合演练：复现、取证、恢复、复盘与口述

**目标**：从组件知识转成一套真实面试可讲、开源用户可复现的 SRE 事件调查流程。所有故障注入仅可在个人隔离集群进行。

## 1. 六个场景与预期证据（不是已完成实测）

| 场景 | 建议注入方式 | 必须收集的反证和恢复证据 |
| --- | --- | --- |
| LAB-01 DNS | 独立命名空间内 DNS 请求限制 / 错误解析配置 | PodIP/ClusterIP 对照、dig、CoreDNS/策略状态 |
| LAB-02 Service | 实验 Service 的错误 targetPort / selector | PodIP vs VIP、EndpointSlice Ready、修复访问 |
| LAB-03 MTU | 独立 VM 上受控降低路径 MTU | 小包/大包、两端 tcpdump、修复验证 |
| LAB-04 NetworkPolicy | 只对实验 Pod 施加拒绝策略 | 策略选择器、入口/出口请求矩阵、拒绝事件 |
| LAB-05 Envoy 503 | 实验路由改向不健康后端 | Response Flags、Route/Cluster/Endpoint、恢复 |
| LAB-06 Istio mTLS | 实验 namespace 内 STRICT 与明文客户端 | PeerAuth/SDS/连接结果、解除冲突 |

**严禁**只凭 `kubectl get pod` 的 Running 状态、`istioctl analyze` 没告警，或 Hubble 没有 DROP 事件宣布故障排除。

## 2. 运行记录：同一个 Run ID 贯穿三个阶段

当前仓库的采集脚本使用**相对当前工作目录的 `../../evidence`**，因此以下命令必须从具体场景目录执行；`RUN_ID` 必须固定，不能各阶段使用默认时间戳。

~~~bash
cd scenarios/dns
export RUN_ID="lab01-$(date +%Y%m%d-%H%M%S)"
bash ../../scripts/preflight.sh  # 可选：含交互式节点调试
bash ../../scripts/collect-evidence.sh dns baseline

# 在隔离实验环境中按场景 README 注入故障，记录实际操作
bash ../../scripts/collect-evidence.sh dns fault

# 执行场景恢复动作，并人工验证 DNS/业务请求
bash ../../scripts/collect-evidence.sh dns recovery
bash ../../scripts/verify-recovery.sh dns

# 回到仓库根目录时检查 evidence/<RUN_ID>/
~~~

**现有脚本限制**：`verify-recovery.sh` 对部分场景尚无完整主动验证，不能把退出码 0 解释成全部通过；`collect-evidence.sh` 并未自动生成抓包、所有版本信息或最终 RCA。需要人为补充并在报告中标记 NOT VERIFIED。日志可能包含敏感配置、地址或令牌，公开提交前先脱敏。

## 3. 证据强度分级

| 等级 | 例子 | 可以宣称 |
| --- | --- | --- |
| E0 文档/预期 | YAML、测试计划 | “设计了复现方案” |
| E1 控制面 | Service、Policy、xDS 配置快照 | “发现配置状态异常” |
| E2 数据面 | 同请求关联的 tcpdump、Hubble、访问日志 | “观察到某一段实际流量行为” |
| E3 闭环 | 复现 + 注入 + 证据 + 恢复后重复测试 | “在指定版本/环境复现并验证恢复” |

不要将模拟数据、文档示意或孤立命令输出包装为生产实测。

## 4. 现场排障顺序（15–30 分钟训练）

1. **定义影响面**：谁请求谁、什么时候开始、错误率、是否局限节点/namespace/新连接。
2. **提出假设**：至少两个可能原因，写出能证伪每个原因的一条命令。
3. **先只读后修改**：检查对象、路由/规则、抓包与日志，不先重启所有组件。
4. **注入并采集**：每个阶段固定 run-id 和时间窗口，保存命令、退出码、stderr、变更点。
5. **最小恢复**：只回滚本次实验引入的变更，验证没有残留 namespace/策略。
6. **复盘与表达**：对照同一请求做恢复验证，记录“当时误判是什么”。

## 5. 面试 90 秒 STAR 提纲

> **S**：集群中某业务跨节点请求超时，Pod Running 但连接异常；明确版本和影响范围。  
> **T**：保证故障定位准确并最小化恢复风险。  
> **A**：先排除应用/Service/DNS，再基于实际 CNI 模式检查路由和封装；在两端用同一 5 元组与时间窗抓包，证明包在何处消失；仅修改实验故障点。  
> **R**：重复 baseline / fault / recovery 测试，出示抓包与报告。如果没有完成某一步，说明“未验证”，不要给出虚构改善数据。

**最后验收**：7 天内完成七张流量图、六张故障假设卡、至少一个真实 E3 实验、一次 90 秒录音。未部署 Istio/Cilium 时，对应实验诚实标记“待环境准备”。

参考：[SRE Workbook](https://sre.google/workbook/) · [现有报告模板](../../evidence/report-template.md)
