# 第 6 天｜Istio 安全与流量：mTLS、授权、Sidecar 与 Ambient

## 1. 不要混淆三种安全边界

| 能力 | 解决的问题 | 不能替代 |
| --- | --- | --- |
| Kubernetes NetworkPolicy | 按网络/端口条件允许连接 | 服务级身份与 HTTP 方法授权 |
| Istio PeerAuthentication + mTLS | 对端身份认证、传输加密及接收策略 | 业务角色授权 |
| Istio AuthorizationPolicy | 基于工作负载身份等条件决定允许/拒绝；部分场景支持 L7 | CNI 网络隔离 |

**自动 mTLS 不等于强制 mTLS**。Sidecar 没有显式要求 STRICT 时，一般允许明文与 mTLS（需注意继承层级和版本）。`UNSET` 表示继承，不应机械解释为最终 PERMISSIVE。

## 2. 数据路径：两种模型不能混画

~~~text
Sidecar:
App A → 本 Pod Envoy → (mTLS/策略/路由) → 目标 Pod Envoy → App B

Ambient:
App A → node ztunnel → (L4 身份/安全隧道)
      → [按需 waypoint Envoy 执行 L7] → 目标 ztunnel → App B
~~~

- Ambient 的 **ztunnel 是 L4 数据面**，L7 路由/授权/遥测要评估 waypoint。
- 仅设置 `istio.io/use-waypoint` 不构成安全强制；若 L7 授权必须经过 waypoint，需要额外定义防绕过的 ztunnel L4 授权并实测。
- Ambient 对 `PeerAuthentication` 的支持和 Sidecar 的实际执行路径不同，不能用“Pod 内是否有 Envoy”判断 Ambient 是否保护流量。

## 3. 只读排查命令

~~~bash
kubectl get ns --show-labels
kubectl get peerauthentication,authorizationpolicy -A
kubectl get virtualservice,destinationrule -A
istioctl analyze -n <NAMESPACE>
istioctl proxy-status
istioctl proxy-config secret <SIDECAR_POD> -n <NAMESPACE>
# Ambient 还需确认 ztunnel/waypoint 工作负载和路由绑定
kubectl -n istio-system get pods -o wide
kubectl get gateway -A
~~~

## 4. 实验设计：从明文允许切换到严格策略

在**独立实验 namespace**建立一个有 Sidecar/ambient 的目标服务和一个未加入 mesh 的测试客户端，先确认基线连通；然后在**该 namespace 内**增加针对目标工作负载的 STRICT 策略，记录未加密客户端失败、mesh 客户端成功的证据。恢复策略后再次对照。**不要对整张生产 mesh 直接施加 STRICT**。

故障定位顺序：策略作用域 → 工作负载是否真正注入/加入 Ambient → 证书与 SPIFFE 身份 → Envoy/ztunnel 日志 → 实际明文/mTLS 请求矩阵。

配套：[LAB-06 Istio mTLS](../../scenarios/istio-mtls/)。

**90 秒口述**：“我先识别数据面模式，再区分是 L4 连不上、mTLS 身份/握手失败，还是 L7 授权拒绝。Sidecar 查双方代理与 SDS；Ambient 查 ztunnel、waypoint 及强制经过的策略。用未入 mesh 客户端和正常客户端做差异对照，保存拒绝日志和恢复前后的请求结果。”

参考：[PeerAuthentication](https://istio.io/latest/docs/reference/config/security/peer_authentication/) · [Ambient Overview](https://istio.io/latest/docs/ambient/overview/) · [Waypoint](https://istio.io/latest/docs/ambient/usage/waypoint/)
