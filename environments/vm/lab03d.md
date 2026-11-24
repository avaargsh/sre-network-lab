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

## 后续：真实三 VM 数据面验收（仍待执行）

Stage 2A 的 Router MTU 租约/恢复控制器、Stage 2B 的四接口抓包工具及 90 秒 watchdog 验证入口已经实现。剩余工作是在专用宿主机真实启动三台 VM，执行 Baseline、四点 PCAP、SSH 会话退出后的自动回滚和 HTTP 恢复验收；之后再实现真实 ICMP 黑洞与 TCP/MSS 业务矩阵。

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

# 单独实测 SSH 会话退出后的 90 秒自动回滚（等待约 95 秒）
WATCHDOG_RUN_ID="mtu-watchdog-$(date -u +%Y%m%d%H%M%S)"
python3 environments/vm/vm_mtu_drill.py drill --rollback-mode watchdog --run-id "$WATCHDOG_RUN_ID"
python3 environments/vm/verify_vm_fault.py "evidence/$WATCHDOG_RUN_ID"
~~~

Router 的 Vagrant Provision 会把本仓库中的 root helper 安装到 `/usr/local/libexec/sre-lab03d-router-fault.py`，自动回滚由该已安装的脚本执行，而不是依赖 SSH 会话。

### 安全与故障恢复门禁

1. Host 先只读核查 3 VM 的不同 machine-id/boot-id、VirtualBox oracle 身份、独立管理网卡、私有 IP、路由及合成 64 KiB HTTP Baseline。
2. Router Helper 独立确认实验标记、两条私有接口、ip_forward、原始 MTU 必须为 1500，且无正在执行的租约。
3. **先写 root-only 的运行租约、注册 90 秒 systemd transient timer 并确认其 ACTIVE，再修改 Router 私有接口 MTU 为 1200**；任一检查失败立即拒绝注入。
4. MTU 改变后重验定时器和接口当前值，执行小 HTTP /health 与 64 KiB POST。此阶段**不丢弃 ICMP Fragmentation Needed**，不能要求大 HTTP 必然超时，更不能冒称已观察 TCP 黑洞。
5. 默认 `--rollback-mode manual` 会提前 SSH 恢复并验收 HTTP。新增 `--rollback-mode watchdog` 会在 arm 命令的 SSH 进程退出后，不再向 Router 发起 SSH 请求，等待完整 90 秒租约加 5 秒宽限，再重新连接执行只读 inspect；只有看到租约已消失、MTU=1500 且 timer inactive，才记为 watchdog PASS。若证据不满足，会尝试人工恢复作为安全兜底，但整个 watchdog 实验仍为 NOT_VERIFIED。

系统只接受当前合法 Run ID 的操作。租约归属不符、timer 未活动、试图选择管理接口、恢复 MTU 未经读回确认，均不返回 PASS。

### 故障时核查

~~~bash
cd environments/vm
vagrant ssh router -c 'ip -br link'
vagrant ssh router -c 'sudo -n systemctl list-timers --all --no-pager | grep sre-lab03d || true'
# 确认是自己的 Run ID 后再执行：
vagrant ssh router -c "sudo -n python3 /usr/local/libexec/sre-lab03d-router-fault.py restore $RUN_ID"
~~~

证据位于 `evidence/<RUN_ID>`：Router 租约、故障窗口回执、`watchdog.json`（实际等待时间、过期后的 Router inspect）、故障和恢复的 HTTP 结果、独立虚拟机拓扑与 SHA256SUMS。通过 `verify_vm_fault.py` 复验。人工模式的 watchdog 维度保持 NOT_VERIFIED；watchdog 模式必须有至少 95 秒单调时钟等待、至少 90 秒记录窗口、`NO_ACTIVE_LEASE`、MTU=1500、timer inactive 及 systemd-watchdog 来源的恢复回执，缺一不可。此阶段 `pcap`、`blackhole`、`network` 三项仍强制标为 NOT_VERIFIED。

**Watchdog 验收入口已实现，真实 VM 实测仍待执行**：在专用三 VM 宿主机上先完成 Stage 1 Baseline，再运行下面的 watchdog 模式。GitHub-hosted CI 只覆盖控制流程和证据审计，不会启动 VirtualBox Guest。没有专用 Runner 的真实 Artifact，不能宣称 SSH 断连自动回滚已经实测。三 VM 多点 PCAP、ICMP 丢失的真实黑洞及 TCP/MSS 恢复仍待后续完成，Issue #6 继续 OPEN。


## Stage 2B：三独立 VM 四接口抓包基线（程序已实现，真实环境待验证）

本阶段不更改接口 MTU，也不注入防火墙规则。先用真实合成 HTTP 流量验证四个捕获点：

| Guest | 捕获私网 IP | PCAP 文件 |
| --- | --- | --- |
| src | 10.209.10.2 | `src.pcap` |
| router 左口 | 10.209.10.1 | `router-left.pcap` |
| router 右口 | 10.209.20.1 | `router-right.pcap` |
| dst | 10.209.20.2 | `dst.pcap` |

Guest 仅允许运行固定 TCP/18080、源/目标地址的 BPF；systemd 强制最长 120 秒和每接口 512 包。每份 PCAP 限 256 KiB，不能指定任意宿主机、接口或过滤器。需在隔离 VirtualBox 实验宿主机操作。

~~~bash
cd environments/vm
vagrant up --provider=virtualbox
for role in src router dst; do vagrant provision "$role"; done
cd ../..

RUN_ID="pcap-vm-$(date -u +%Y%m%d%H%M%S)"
python3 environments/vm/lab_vm.py baseline --run-id "$RUN_ID-pre"
python3 environments/vm/vm_pcap_baseline.py baseline-pcap --run-id "$RUN_ID"
python3 environments/vm/verify_vm_pcap.py "evidence/$RUN_ID"
~~~

Host 控制器先验证三台 VM 的独立机器/启动身份、管理网卡隔离、双向路由和 HTTP 64 KiB POST；然后分别启动三 VM 上四个限时捕获服务，运行新的 GET/POST，最后停止并导出四份 PCAP。即使发生 HTTP 错误，也会尝试停止已启动的捕获服务；最长存活时间由 systemd 控制。

证据存放在 `evidence/<RUN_ID>`：三 VM 拓扑、HTTP JSON、四个二进制 PCAP、四接口元数据、SHA256SUMS 与状态。独立校验要求**同一个客户端 TCP SYN**（同源端口和初始 Seq）在四份文件中都能观察到，并在四处观察到该流量的数据段，同时重新验证 HTTP 和全部文件哈希。校验失败时不能留存 PASS 状态。

**证据边界**：截至本次提交，没有三台真实 VirtualBox VM 的跨机 PCAP Artifact。GitHub Actions 只验证合成数据与代码逻辑。Router MTU 注入、真实三 VM 的 90 秒回滚、PMTU 黑洞和 MSS 缓解仍未完成实测，Issue #6 保持 OPEN。


## Stage 2C：精确 HTTP POST 四点序号相关性 + 手动真实 VM 验收

相比原有只检查相同 SYN 和任意 TCP 数据的机制，新独立校验器明确要求：四份原始 PCAP 必须有同一客户端 SYN 的端口与初始 Seq；四处必须观察到**同一序号的 POST /upload HTTP/1.x 请求头**；并且四处必须在该连接中观察到至少 **512 字节相互重合的上传 TCP 序号范围**，同时容许 Router 重新分段。仅有 GET /health、缺少 POST 头、POST 序号不一致或大数据不再转发，一律 NOT_VERIFIED。

抓包 snaplen=160，因此只解析有限的 HTTP 请求头前缀，线上 TCP 长度取 IPv4 Total Length。校验器并不假装保存了完整 HTTP body；完整 HTTP 回执与服务器内容 SHA256 需要额外独立核对。

### 专用自托管 Runner：手动真实三 VM E2E

[手动真实 VM GitHub Actions 工作流](../../.github/workflows/lab03d-real-vbox.yml) 仅支持 `workflow_dispatch`，在 `main` 上手动运行，要求固定确认字符串 `I_CONFIRM_DEDICATED_VBOX_HOST`。该工作流必须由维护者配置**专用的 Linux 自托管 Runner**，标签 `sre-vbox-lab`，预安装 VirtualBox、Vagrant、Python3，并具备可启动 3 台 VM 的宿主机资源。

运行 `environments/vm/run_live_e2e.sh` 之前会检查明确批准、实验 Scope、非 root 身份、固定 Vagrant 工作目录、没有现存 .vagrant 状态、无其他运行中的 VirtualBox 虚拟机，以及 src/router/dst 均处于 not_created。任一条件不满足拒绝启动。

通过预检后执行以下真正的 E2E：

1. 在专用宿主机执行 `vagrant up --provider=virtualbox`；
2. 用 `lab_vm.py baseline` 和 `verify_vm_evidence.py` 核验三个独立 VM 的真实身份、私有路由和合成 HTTP；
3. 用 `vm_pcap_baseline.py baseline-pcap` 和 `verify_vm_pcap.py` 获取并检查四个原始 PCAP、真实 POST 头、共通 TCP 上传数据区间和 SHA-256；
4. 上传仅含合成实验流量的 Artifact（默认保留 14 天），并尝试 `vagrant destroy -f` 回收本次新建的 VM。

清理 Hook 无法覆盖宿主机突然断电或 Runner 被强杀的情况，此时需要人工核对 VirtualBox 资源。不要把共享生产宿主机注册为该 Runner；不要绕过身份检查或强制对已有 VM 运行实验。

**真实执行状态**：截至该 PR，没有配置可执行的专用 VirtualBox Runner，GitHub-hosted CI 只覆盖代码和合成报文测试。因此真实三 VM 的身份/HTTP/四点 PCAP E2E 仍未验证。工作流默认只执行 Baseline/PCAP；只有另行填写第二确认 `I_CONFIRM_ROUTER_MTU_WATCHDOG` 才会执行 90 秒 watchdog 故障实验。它不注入 ICMP 丢包，也不代表 PMTU 黑洞或 MSS 缓解已通过；没有真实 Runner Artifact 前，不得宣称 VM watchdog 实测通过。Issue #6 保持开放。
