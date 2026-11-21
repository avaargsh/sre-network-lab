# Kind E2E integration (LAB-01 DNS and LAB-02 Service)

The workflow `/.github/workflows/kind-e2e.yml` creates a disposable Kind cluster on a GitHub-hosted Ubuntu runner, runs both labs, and uploads evidence from that **ephemeral test cluster only**.

Scope:
- Confirm 3 Kind nodes Ready and CoreDNS deployment rolled out.
- LAB-01: baseline PodIP/VIP/FQDN available; client-only DNS fault; restore verified.
- LAB-02: baseline PodIP/VIP/FQDN available; wrong targetPort; restore verified.
- Evidence from all three phases with `phase-status.json`, `command-status.tsv`, and `SHA256SUMS`.
- Runtime traps attempt recovery and named-resource cleanup even when a step fails.

The workflow is not proof of cross-node traffic forwarding or Overlay/Underlay behavior, and does not exercise Calico/Cilium, NetworkPolicy, Envoy, or Istio. It does not change a real cluster. It is intended as a narrow, reproducible Kind integration gate.

## Verification protocol

1. Inspect the workflow result and `kind-network-evidence` artifact.
2. Check each scenario has exactly three phases and `probe_status: PASS` in each `phase-status.json`.
3. Check `command-status.tsv` for any auxiliary diagnostic command failures; a successful probe does not mean all optional evidence is present.
4. Run `sha256sum -c SHA256SUMS` separately inside each phase folder.
5. If the workflow fails, inspect the pod descriptions and `lab-probe.err` from the uploaded artifact, fix the root cause, and rerun; do not claim GREEN prematurely.

**CI artifacts may include IPs and cluster configuration.** Only use synthetic disposable resources in this workflow; never auto-upload evidence collected from a real production cluster.

## 真实 Kind 失败复盘（首次 E2E）

首次 hosted Kind（Kubernetes v1.37.0）中，故障注入阶段成功，但恢复后对 DNS 的检测仍使用 localhost `127.0.0.1`。原因是旧版脚本从 Pod 列表固定选取 `.items[0]`：滚动更新完成时，旧 Pod 可能仍在终止期。修复后改为从 Kubernetes Pod JSON 中过滤 `deletionTimestamp == null`、`phase == Running`、`Ready == True`，并加入旧/新 Pod 同时存在的回归测试。

复盘的证据边界：此前运行并非 GREEN，修复是否有效须以**后续新 run** 的 Kind 阶段状态及请求证据确认。
