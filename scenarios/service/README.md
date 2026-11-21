# LAB-02 · Service targetPort 故障（可执行实验）

**目的**：证明 Pod IP 正常而 ClusterIP / Service DNS 失败时，可能是 Service targetPort 配置错误，不应直接归咎于 CNI 或 CoreDNS。

## 实验约束

- 只在个人隔离 Kind / VM 集群运行；`scripts/lab-service.sh` 会在专用 `sre-lab-service` Namespace 下创建两个 Deployment 和一个 Service。
- 使用 `hashicorp/http-echo:1.0.0` 与 `nicolaka/netshoot:v0.14`，需能够拉取镜像。
- 需要 kubectl 有 Namespace、Deployment、Pod、Service 的写入和 exec 权限；变更操作必须显式设置 `SRE_LAB_ALLOW_MUTATIONS=1`。创建的 Namespace 会被保留，以免误删用户资源。
- `fault` 阶段**预期** Service 请求失败、直连 Pod IP 成功；这是有意注入的错误，不应当在生产环境应用。

## 流程

在仓库根目录执行：

```bash
export SRE_LAB_ALLOW_MUTATIONS=1  # 必须确认 kubectl current-context 指向隔离集群
scripts/lab-service.sh setup
scripts/lab-service.sh baseline
export RUN_ID="service-$(date -u +%Y%m%dT%H%M%SZ)"
scripts/collect-evidence.sh service baseline

scripts/lab-service.sh inject
scripts/lab-service.sh fault
scripts/collect-evidence.sh service fault

scripts/lab-service.sh restore
scripts/lab-service.sh verify
scripts/verify-recovery.sh service
scripts/collect-evidence.sh service recovery
scripts/lab-service.sh cleanup
```

注入动作：将 Service 的 `targetPort: http`（容器监听 5678）更改成 `5679`，后端 Pod、readiness 与 EndpointSlice 地址集合保持不变。`fault` 同时用 `http://PodIP:5678/`、`http://ClusterIP:80/`、Service DNS 做对照；`verify` 要求三者都恢复成功。

通过标准：baseline 三项成功，fault 阶段 PodIP 成功且 VIP/DNS 请求失败，restore 后三项成功；证据中包含 `Service` 配置、EndpointSlice 和各阶段命令结果。当前采集器会运行对应阶段的探测脚本，将结果写入 `lab-probe.out/.err`，并生成 `phase-status.json`、`command-status.tsv` 与 `SHA256SUMS`；探测失败会使采集器非零退出。真实抓包与完整 RCA 仍需人工补足，不能仅凭离线 CI 声称 E3 实测通过。

失败时不要执行 `cleanup` 之前的自动集群级修复。若在注入后中断，先运行 `scripts/lab-service.sh restore`，再 `verify`；若 Service 的 targetPort 已被其他人修改，脚本会拒绝覆盖，需人工确认。

## 诊断要点

- 确认 `kubectl -n sre-lab-service get svc lab-echo -o yaml` 的 `targetPort`。
- 确认 `kubectl -n sre-lab-service get endpointslices -l kubernetes.io/service-name=lab-echo -o yaml` 的 Endpoint / Ready。
- 解释为何 PodIP 直连正常但 Service VIP 失败：Service 选择了正确 Pod，但转发到错误端口。
- 不同 Service 代理模式下，实际 DNAT/BPF Hook 不同；本实验**不证明某种代理实现的性能或故障行为**。

## 静态与模拟测试

```bash
bash -n scripts/*.sh
python3 -m unittest discover -s tests -v
```

这些测试仅验证脚本状态转换与安全约束；真实 Kubernetes E2E 仍需在独立 Kind/VM 中进行。
