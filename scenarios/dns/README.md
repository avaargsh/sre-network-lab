# LAB-01: DNS 故障

## 目标

验证域名解析失败时的排查路径，区分 CoreDNS 问题与网络连通性问题。

## 注入故障

```bash
# 示例：破坏 CoreDNS ConfigMap 或用 NetworkPolicy 阻断 53
kubectl -n kube-system get configmap coredns -o yaml > /tmp/coredns-backup.yaml
# 修改后 apply，或创建拒绝策略
```

## 验收证据

- `nslookup` / `dig` 失败
- CoreDNS Pod 日志
- 恢复后解析成功

## 关键命令

```bash
kubectl -n kube-system get pods -l k8s-app=kube-dns
kubectl -n kube-system logs -l k8s-app=kube-dns --tail=50
kubectl run netshoot --rm -it --image=nicolaka/netshoot -- nslookup kubernetes.default
```

## 报告模板

见 `../../evidence/<run-id>/report.md`
