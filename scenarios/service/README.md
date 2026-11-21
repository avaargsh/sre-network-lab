# LAB-02: Service 转发故障

## 目标

区分 Pod IP 连通性与 ClusterIP / Service 负载均衡问题。

## 注入故障

- 错误的 `targetPort`
- 后端 Pod readiness 失败
- EndpointSlice 未更新

## 验收证据

- Pod IP 可访问，ClusterIP 不可访问
- `kubectl get endpointslices` 显示后端状态
- 恢复后 Service 访问成功

## 关键命令

```bash
kubectl get svc,endpointslices -n <ns>
kubectl describe svc <svc> -n <ns>
# 在工具 Pod 内
curl -sv --connect-timeout 3 http://<clusterip>:<port>
curl -sv --connect-timeout 3 http://<pod-ip>:<port>
```
