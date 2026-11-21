# 第 5 天｜Envoy：Listener、Filter Chain、Route、Cluster 与 xDS

## 1. 在白板上画出 L7 请求路径

~~~text
Client → Downstream TCP/TLS → Listener / Filter Chain
    → HTTP Connection Manager (HCM)
    → VirtualHost + Route 匹配
    → Cluster → LB → Endpoint → Upstream TCP/TLS → Backend
~~~

| 组件 | 回答的问题 | 常见错误 |
| --- | --- | --- |
| Listener / Filter Chain | 在什么端口/协议/SNI 接收连接？ | 端口、SNI、TLS 匹配错误 |
| Route / VirtualHost | Host/Path/Header 匹配到哪一条规则？ | 无路由 NR |
| Cluster | 上游服务及连接策略是什么？ | 上游不可达、连接池/熔断 |
| Endpoint | 上游具体地址、健康状态如何？ | 零健康后端 UH |
| HTTP Filter | JWT、鉴权、重写等在何处执行？ | 拒绝不是后端故障 |

LDS/RDS/CDS/EDS/SDS 分别服务于 Listener、Route、Cluster、Endpoint 和 Secret；**ADS 是聚合分发方式，不是额外的业务流量组件**。控制面推送成功 ≠ 数据面请求成功。NACK 后 Envoy 可能继续使用上一个有效版本。

## 2. 503 诊断矩阵

| 观察 | 初始假设 | 下一份证据 |
| --- | --- | --- |
| `NR` No Route | Route / Host / Path 不匹配 | Route 配置与 Host Header |
| `UH` No Healthy Upstream | 可用上游为空 | Cluster / Endpoint 健康状态 |
| `UF` Upstream Failure | TCP/TLS/网络连接失败 | upstream 连接错误与握手事件 |
| `UO` Overflow | 连接池或熔断限制 | 相关统计、并发与资源限制 |

响应标志是**定位线索**而非最终根因，需结合访问日志、配置和请求重放。没有响应标志的原始日志时不要写“确定为 UH”。

## 3. 在 Istio 环境观察真实 Envoy 配置

~~~bash
istioctl proxy-status
istioctl proxy-config listeners <POD> -n <NAMESPACE>
istioctl proxy-config routes <POD> -n <NAMESPACE>
istioctl proxy-config clusters <POD> -n <NAMESPACE>
istioctl proxy-config endpoints <POD> -n <NAMESPACE>
istioctl analyze -n <NAMESPACE>
~~~

替换占位符并确保该 Pod 确实有 Envoy Sidecar；若使用 Envoy Gateway/裸 Envoy，应改为其 Admin API 和对应控制器的诊断方式，不能照搬 `istioctl`。

## 4. 练习题与验收

- 练习 1：请求 Host 不匹配、上游无健康实例、上游拒绝连接，分别应该从哪一层开始找证据？
- 练习 2：RDS 更新被 NACK，但旧路由还能正常用，为什么？
- 练习 3：为什么重试会放大下游请求压力？需要同时看超时、retry budget/次数与后端容量。

配套：[LAB-05 Envoy 503](../../scenarios/envoy-503/)。

**90 秒口述**：“我按 Listener→Route→Cluster→Endpoint 排查，先确认请求是否真的到达 Envoy，再看具体 Response Flags 和 xDS 状态。NACK 可能导致旧配置继续生效；下一步用相同 Host/Path 重放并对照 upstream 连接日志，找到根因后验证配置和请求结果均恢复。”

参考：[Envoy 架构](https://www.envoyproxy.io/docs/envoy/latest/intro/arch_overview/arch_overview) · [xDS API](https://www.envoyproxy.io/docs/envoy/latest/api-docs/xds_protocol)
