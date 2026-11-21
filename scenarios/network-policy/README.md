# LAB-04: NetworkPolicy 拒绝

## 目标

验证入口/出口分别隔离，以及策略叠加行为。

## 注入故障

创建拒绝特定源或目标的 NetworkPolicy。

## 验收证据

- 策略对象存在
- Hubble / Calico 日志显示 DROP
- 删除策略后流量恢复

## 关键命令

```bash
kubectl get networkpolicy -A
kubectl describe networkpolicy <name> -n <ns>
# Cilium
hubble observe --verdict DROPPED --since 5m
# Calico
kubectl -n kube-system logs -l k8s-app=calico-node | grep deny
```
