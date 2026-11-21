# LAB-03D · 三独立 VirtualBox VM 的 Underlay 基线

本阶段实现 **src、router、dst 三台独立 VM + 两段互相隔离的 VirtualBox Internal Network**，并提供只读拓扑及真实 HTTP 请求的验收门禁。

**验证边界**：代码与离线测试不等于真实多 VM 运行。当前 PR 的 GitHub-hosted CI 并未启动三台 VirtualBox VM，也未注入 VM 网络故障。实际三 VM Baseline、PCAP 和故障恢复均为 NOT VERIFIED。

## 拓扑

~~~text
src VM 10.209.10.2/24  →  10.209.10.1/24 router VM
                                     IPv4 forwarding
dst VM 10.209.20.2/24  ←  10.209.20.1/24 router VM

左侧网络：VirtualBox Internal Network sre-lab03d-left
右侧网络：VirtualBox Internal Network sre-lab03d-right
~~~

三台 VM 的 Vagrant NAT 管理网卡仅用于 SSH/安装依赖；实际业务请求必须经过两段 Internal Network。路由分别为：src 去 10.209.20.0/24 via 10.209.10.1；dst 去 10.209.10.0/24 via 10.209.20.1；router 启用 IPv4 forwarding。

## 在独立 Linux 宿主机启动

需要 VirtualBox、Vagrant、硬件虚拟化和三台 VM 的资源。当前默认 Box 为 `bento/ubuntu-24.04`，请预先核对来源与版本；支持使用 `SRE_LAB_BOX`、`SRE_LAB_BOX_VERSION` 显式选择并固定 Box。VirtualBox 共享目录需要 Guest Additions。

~~~bash
cd environments/vm
vagrant up --provider=virtualbox
vagrant status
vagrant ssh src -c 'ip -4 route get 10.209.20.2'
vagrant ssh dst -c 'ip -4 route get 10.209.10.2'
cd ../..

export RUN_ID="vm-topology-$(date -u +%Y%m%dT%H%M%SZ)"
python3 environments/vm/lab_vm.py attest --run-id "$RUN_ID"
python3 environments/vm/lab_vm.py baseline --run-id "$RUN_ID-http"
python3 environments/vm/verify_vm_evidence.py "evidence/$RUN_ID-http"

# 只有确认当前在该专用 Vagrant 工作目录后才清理：
cd environments/vm
vagrant destroy -f
~~~

`attest` 只检查拓扑和身份，不执行业务。`baseline` 检查全部 VM 身份/路由后，由 src 向 dst 的 18080 端口执行真实 GET /health 与 64KiB POST；目标 VM 必须返回完整合成请求的 SHA256。

成功的每个运行写入不同的 `evidence/<RUN_ID>/`，包含 vm-attestations.json、http.json、status.json、SHA256SUMS，且不覆盖同名证据。该目录已被 Git 忽略。重启后如果路由缺失，可以在专用 Vagrant 环境运行 `vagrant provision` 重新配置后再验收。

## Fail-closed 条件

- 独立的 src/router/dst 三个角色，具有不同的 machine-id 和 kernel boot_id。
- Guest 必须有 Provisioning 角色标记，且 `systemd-detect-virt --vm` 为 `oracle`（VirtualBox）；此标记不属于加密身份凭证。
- 固定实验子网地址必须完全匹配；内部数据网卡不得是默认管理路由接口。
- 源/目标两侧到对端必须经 Router 的左/右接口，Router 的 ip_forward 必须为 1。
- HTTP 不能只检查 TCP 能连接，还要检查 200、health 正文、64KiB POST 的 SHA256。
- 控制器不支持任意 SSH Host/目标 IP/故障命令，本阶段不会修改运行中的 VM 或宿主机网络规则。

## 下一阶段：三 VM 实际 MTU 故障（仍未实现）

独立 VM Baseline 通过后再实现：Router 的指定实验网卡 MTU 限制、SSH 断连仍能执行的有时限自动回滚、src/router/dst 三点抓包、TCP/ICMP 业务矩阵、恢复后业务重验以及可信证据归档。

这些都需要在实际三 VM 中执行并验收，**不能用单机 Namespace 的绿色 CI 代替**，Issue #6 保持开放。

参考：[Vagrant VirtualBox Internal Networks](https://developer.hashicorp.com/vagrant/docs/providers/virtualbox/networking)。


## Stage 2A — 有限 MTU 故障租约（代码完成，真实 VM 待验收）

本阶段只在 Router Guest 的 `10.209.20.1` 私有数据接口模拟 MTU 1500→1200，不触及宿主机或 Vagrant NAT 管理接口。**当前无三台真实 VirtualBox VM 的运行证据，不能声称自动回滚已实测。**

必须先完成 Stage 1 的三 VM 身份、双向路由及 HTTP Baseline 验收。

### 在专用 VirtualBox 测试环境运行

~~~bash
cd environments/vm
vagrant provision router
cd ../..

export SRE_LAB_VM_FAULT_ACK=1
export SRE_LAB_VM_SCOPE=sre-lab03d-vbox-intnet
export RUN_ID="mtu-vm-$(date -u +%Y%m%d%H%M%S)"

python3 environments/vm/vm_mtu_drill.py drill --run-id "$RUN_ID"
python3 environments/vm/verify_vm_fault.py "evidence/$RUN_ID"
~~~

Router 的 Vagrant Provision 会把本仓库中的 root helper 安装到 `/usr/local/libexec/sre-lab03d-router-fault.py`，自动回滚由该已安装的脚本执行，而不是依赖 SSH 会话。

### 安全与故障恢复门禁

1. Host 先只读核查 3 VM 的不同 machine-id/boot-id、VirtualBox oracle 身份、独立管理网卡、私有 IP、路由及合成 64 KiB HTTP Baseline。
2. Router Helper 独立确认实验标记、两条私有接口、ip_forward、原始 MTU 必须为 1500，且无正在执行的租约。
3. **先写 root-only 的运行租约、注册 90 秒 systemd transient timer 并确认其 ACTIVE，再修改 Router 私有接口 MTU 为 1200**；任一检查失败立即拒绝注入。
4. MTU 改变后重验定时器和接口当前值，执行小 HTTP /health 与 64 KiB POST。此阶段**不丢弃 ICMP Fragmentation Needed**，不能要求大 HTTP 必然超时，更不能冒称已观察 TCP 黑洞。
5. Host 无论成功或失败都尝试提前恢复 MTU 1500 并进行 HTTP 恢复验收。若 SSH 中断，预先注册的 systemd timer 应在租约期满时尝试恢复；若无法取得实际确认，结果仍为 NOT VERIFIED。

系统只接受当前合法 Run ID 的操作。租约归属不符、timer 未活动、试图选择管理接口、恢复 MTU 未经读回确认，均不返回 PASS。

### 故障时核查

~~~bash
cd environments/vm
vagrant ssh router -c 'ip -br link'
vagrant ssh router -c 'sudo -n systemctl list-timers --all --no-pager | grep sre-lab03d || true'
# 确认是自己的 Run ID 后再执行：
vagrant ssh router -c "sudo -n python3 /usr/local/libexec/sre-lab03d-router-fault.py restore $RUN_ID"
~~~

证据位于 `evidence/<RUN_ID>`：Router 租约、定时器回执、故障和恢复的 HTTP 结果、独立虚拟机拓扑与 SHA256SUMS。通过 `verify_vm_fault.py` 复验；此阶段 `pcap`、`blackhole`、`network` 三项仍强制标为 NOT_VERIFIED。

**后续阶段**：在真 VM 上验收 90 秒 watchdog（含 SSH 中断）、三 VM 多点 PCAP、ICMP 丢失的真实黑洞及 TCP/MSS 恢复。没有这些独立证据，Issue #6 继续 OPEN。
