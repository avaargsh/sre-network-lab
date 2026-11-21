# LAB-01 · DNS 客户端配置故障（自动化实验）

**边界**：本实验仅修改专用 Namespace `sre-lab-dns` 中的 Probe Deployment `dnsPolicy` 和 `dnsConfig`，不会修改 CoreDNS、节点网络或其他业务 Pod。验证的是客户端解析器配置故障，**不是 CoreDNS 整体故障**。

## 前置条件

- 专用 Kind 或 VM Kubernetes 实验集群、kubectl、jq、HTTP Echo 和 Netshoot 镜像拉取能力。
- 先运行 `kubectl config current-context`，确认连接的不是生产集群。
- 需显式 `SRE_LAB_ALLOW_MUTATIONS=1` 才能修改实验资源；命名空间保留以避免误删额外资源。
- 采集三个阶段时使用同一个 `RUN_ID`。

## 一键分步复现（仓库根目录）

~~~bash
kubectl config current-context
export SRE_LAB_ALLOW_MUTATIONS=1
export RUN_ID="dns-$(date -u +%Y%m%dT%H%M%SZ)"

scripts/lab-dns.sh setup
scripts/lab-dns.sh baseline
scripts/collect-evidence.sh dns baseline

scripts/lab-dns.sh inject
scripts/lab-dns.sh fault
scripts/collect-evidence.sh dns fault

scripts/lab-dns.sh restore
scripts/lab-dns.sh verify
scripts/verify-recovery.sh dns
scripts/collect-evidence.sh dns recovery

scripts/lab-dns.sh cleanup
~~~

运行中断且已经注入故障时，应先 `scripts/lab-dns.sh restore` 再 `verify`。脚本有状态守卫，若策略与预期不符则拒绝恢复，避免覆盖未知操作。

## 请求矩阵与因果说明

| 来自同一 Probe Pod | 正常 | DNS 故障 | 恢复 |
| --- | --- | --- | --- |
| PodIP:5678 | HTTP 200，响应正文匹配 | 成功 | 成功 |
| ClusterIP:80 | 成功 | 成功 | 成功 |
| Service FQDN nslookup | 成功 | 非零退出 | 成功 |
| Service FQDN curl | 成功 | **curl 退出码 6** | 成功 |

注入动作：把测试客户端从 `ClusterFirst` 切到 `None`，设置仅有的 nameserver 为 `127.0.0.1`，等待 Pod 重建。这个故障不依赖 CNI 支持 NetworkPolicy，可以在 Kind 默认网络模式下复现。**由于查询未到达 CoreDNS，CoreDNS 日志没有错误是可能且合理的。**

## 证据与边界

`scripts/collect-evidence.sh dns <phase>` 保存三阶段的 `lab-probe.out/.err`、`phase-status.json`、`command-status.tsv`、`SHA256SUMS` 和集群配置快照。主要结论从同一个客户端的 PodIP/ClusterIP/FQDN 请求对照得出，不根据 Pod Running 或 DNS 日志单独下结论。

`phase-status.json` 仅表示当前脚本对指定条件的主动探测是否通过。未经真实 Kind/VM 测试、抓包与版本复现前，不可称为生产级 E3 实证。

参考：[Kubernetes Pod DNS 策略](https://kubernetes.io/docs/concepts/services-networking/dns-pod-service/) · [证据报告模板](../../evidence/report-template.md)
