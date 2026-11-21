# LAB-06: Istio mTLS 冲突

## 目标

验证 PERMISSIVE 与 STRICT 模式差异，以及握手失败的证据。

## 注入故障

一侧 STRICT，另一侧无 Sidecar 或 DISABLE。

## 验收证据

- PeerAuthentication 对象
- 代理 TLS 状态
- 握手失败日志与恢复后成功

## 关键命令

```bash
kubectl get peerauthentication -A
istioctl proxy-config secret <pod> -n <ns>
# 查看 Envoy 日志中的 TLS 事件
```
