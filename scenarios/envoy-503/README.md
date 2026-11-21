# LAB-05: Envoy 503

## 目标

定位 Envoy 返回 503 的具体原因（NR / UH / UF / UO）。

## 注入故障

错误的 VirtualService 路由、无健康上游、连接失败等。

## 验收证据

- `istioctl proxy-config routes/clusters/endpoints`
- Envoy 访问日志中的响应标志
- 修复配置后请求成功

## 关键命令

```bash
istioctl proxy-status
istioctl proxy-config routes <pod> -n <ns>
istioctl proxy-config clusters <pod> -n <ns>
istioctl proxy-config endpoints <pod> -n <ns>
```
