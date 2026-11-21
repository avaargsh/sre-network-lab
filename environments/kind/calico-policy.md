# Calico 专用 Kind 实验环境（LAB-04）

**不要在已安装 kindnet 的集群上叠加 Calico。** 本实验创建专用三节点 Kind（1 Control Plane + 2 Workers），关闭默认 CNI，Pod CIDR 固定为 192.168.0.0/16，并通过 Operator 明确指定 VXLAN 封装。

| 项目 | 固定配置 |
| --- | --- |
| Kind 集群名称 | sre-policy-lab（context: kind-sre-policy-lab） |
| Calico | v3.33.1，commit d95e175f6be5a0e8ed29645ac8faedf08bd800e7 |
| Pod CIDR / IPPool | 192.168.0.0/16 |
| Encapsulation | VXLAN Always（预期；要核对实际 IPPool） |
| kube-proxy | 保留 Kind 自带 kube-proxy，不使用 eBPF 替代 |

## 手动创建（仅隔离测试机器）

安装 Docker、kind、kubectl、jq，然后在仓库根目录执行：

~~~bash
kind create cluster --name sre-policy-lab --config environments/kind/calico.yaml
kubectl config current-context
kubectl get nodes -o wide  # 尚无 CNI，NotReady 是正常的

CALICO_COMMIT=d95e175f6be5a0e8ed29645ac8faedf08bd800e7
BASE="https://raw.githubusercontent.com/projectcalico/calico/$CALICO_COMMIT/manifests"
curl -fsSL "$BASE/v3_projectcalico_org.yaml" -o /tmp/calico-crds.yaml
curl -fsSL "$BASE/tigera-operator.yaml" -o /tmp/tigera-operator.yaml
kubectl create -f /tmp/calico-crds.yaml
kubectl create -f /tmp/tigera-operator.yaml
kubectl wait --for=condition=Established crd/installations.operator.tigera.io --timeout=180s
kubectl apply -f environments/kind/calico-installation.yaml

kubectl -n calico-system rollout status daemonset/calico-node --timeout=480s
kubectl wait --for=condition=Ready node --all --timeout=240s
kubectl get ippools.projectcalico.org -o yaml
~~~

**真实验收**：需确认三个节点 Ready、calico-node 正常、IPPool VXLAN 模式与安装对象一致。安装失败时禁止继续策略实验；在生产机器上不允许随意卸载或重新安装集群网络。

仓库提供 [独立 Calico E2E Workflow](../../.github/workflows/calico-policy-e2e.yml)，在临时 Kind 集群做五阶段请求矩阵并上传证据。运行状态要以对应 Actions 的实际结果为准，不能根据流程定义直接宣称 GREEN。

实验后删除**整个明确命名的临时集群**：

~~~bash
kind delete cluster --name sre-policy-lab
~~~

官方参考：[Calico on Kind](https://docs.tigera.io/calico/latest/getting-started/kubernetes/kind)。
