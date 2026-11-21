# LAB-03: 跨节点 MTU 故障

## 目标

复现 PMTU 黑洞，验证不同报文大小下的行为差异。

## 注入故障

调低节点或隧道接口 MTU，发送大于 MTU 的报文。

## 验收证据

- 小包成功，大包失败
- `tcpdump` 捕获封装后的报文
- 恢复 MTU 后大包成功

## 关键命令

```bash
ip link show | grep mtu
ping -M do -s 1400 <dest-pod-ip>
sudo tcpdump -ni <iface> host <dest-pod-ip>
```
