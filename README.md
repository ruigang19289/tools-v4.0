# Tools

一个基于 Django + Vue 3 的 DevOps 工具平台，提供性能测试、系统监控、网络工具和系统初始化能力。

## 功能概览

当前项目包含 8 个可用模块：

### 测试工具
- `VDBench 可视化`：VDBench 参数配置与结果展示
- `FIO 测试`：多主机 FIO 压测、实时输出、统计聚合与图表展示
- `CosBench 配置文件生成`：生成 workload.xml / controller.conf 配置

### 网络工具
- `网段扫描`：批量扫描主机可达性
- `网络聚合配置`：Bond 聚合配置
- `带宽测试`：基于 iperf3 的多主机带宽测试

### 系统工具
- `系统监控`：远程主机 CPU / 磁盘 / 网络监控（当前为 HTTP 轮询）
- `Ansible 管理平台`：主机验证、批量命令、文件分发、Playbook 执行
- `系统初始化`：主机初始化、主机名批量生成与配置

## 技术栈

### 后端
- Django 4.2+
- Django REST Framework
- Django Channels
- Daphne (ASGI)
- SQLite
- Paramiko

### 前端
- Vue 3.4+
- Vue Router
- Pinia
- Vite 5
- Axios
- Chart.js

## 目录结构

```text
backend/apps/
├── performance/
│   ├── vdbench/
│   ├── monitor/
│   ├── FIOTest/
│   └── CosBenchGenerator/
├── network/
│   ├── PingScan/
│   ├── bond/
│   └── NetworkReliabilityTest/
└── system/
    ├── ansible/
    └── system_init/

frontend/src/
├── apps/
│   ├── performance/
│   │   ├── VDBench.vue
│   │   ├── Monitor.vue
│   │   ├── FIOTest.vue
│   │   └── CosBenchGenerator.vue
│   ├── network/
│   │   ├── PingScan.vue
│   │   ├── BondConfig.vue
│   │   └── NetworkReliabilityTest_new.vue
│   └── system/
│       ├── Ansible.vue
│       └── SystemInit.vue
├── components/common/
├── config/
└── router/
```

## 本地运行

### 启动项目

```bash
./start.sh
```

Docker 镜像建议使用 host 网络运行，以保证容器内 SSH 可直连业务节点：

```bash
# 密码认证（原有方式）
docker run -d --name sds-tools --network host tools-app:v2.0

# SSH 私钥认证：只读挂载宿主机准备好的专用密钥目录。
# 远端主机须已将对应公钥加入目标账号的 authorized_keys。
docker run -d --name sds-tools --network host \
  -v /opt/tools-ssh:/root/.ssh:ro \
  -e TOOLS_ANSIBLE_PRIVATE_KEY_PATH=/root/.ssh/id_ed25519 \
  tools-app:v2.0

> 不要把私钥上传到网页或写入镜像。容器内默认读取 `/root/.ssh/id_ed25519`；若使用其他文件名，通过 `TOOLS_ANSIBLE_PRIVATE_KEY_PATH` 指定。私钥认证要求无交互口令私钥，或由运维侧预先处理其解锁方式。
```

默认端口：
- 后端：`6000`
- 前端：`6500`

### 停止项目

```bash
./stop.sh
```

### 前端开发

```bash
cd frontend
npm run dev
npm run build
```

### 后端开发

```bash
./venv/bin/python manage.py migrate
./venv/bin/daphne -b 0.0.0.0 -p 6000 backend.asgi:application
```

## 通信方式

### HTTP API
所有 REST 接口挂在 `/api/v1/` 下。

### WebSocket
当前项目中已使用的 WebSocket：
- `/api/v1/perf/fio/ws`：FIO 实时输出与统计推送
- `/api/v1/network/iperf3/ws`：带宽测试实时通信

### 开发代理
Vite 开发服务器会将 `/api` 代理到本地 Django 服务。

## 主要页面路由

### 测试工具
- `/perf/vdbench`
- `/perf/monitor`
- `/perf/fio`
- `/perf/cosbench`

### 网络工具
- `/network/ping`
- `/network/bond`
- `/network/iperf3`

### 系统工具
- `/system/ansible`
- `/system/init`

## 关键实现说明

### FIO 测试
- 使用 WebSocket 推送终端输出和实时统计
- 支持多主机并发测试
- 实时展示 IOPS、吞吐量、响应时间图表
- 多主机统计聚合：IOPS / 吞吐量求和，响应时间取平均值
- 页面顶部统计卡片当前布局为：
  - 上排：`I/O 速率 | 响应时间`
  - 下排：`吞吐量 | 运行时长`

### 系统监控
- 通过 `/perf/monitor/connect` 建立 SSH 连接
- 通过 `/perf/monitor/system-info` 每秒轮询获取 CPU / 磁盘 / 网络数据
- 保留连接历史与页面状态

### 带宽测试
- 基于 iperf3 远程执行
- 支持多主机拓扑测试
- 支持测试中断、结果查询和主机连接验证

### 系统初始化
- 支持单个 IP、IP 范围、CIDR 输入
- 根据 IP 排序自动生成主机名
- 默认用户名为 `root`

### Ansible 管理平台
- 支持主机验证
- 支持 shell / command / ping / setup 模块
- 支持文件推送与拉取
- 支持 Playbook 执行与 YAML 校验

## 模块状态配置

模块状态定义在：

```text
frontend/src/config/modules.js
```

可用配置：
- `vdbench`
- `monitor`
- `fio`
- `cosbench`
- `ping`
- `bond`
- `iperf3`
- `ansible`
- `system_init`

## API 返回格式

```python
# 成功
{'status': 'success', 'data': {...}}

# 错误
{'status': 'error', 'error': 'message'}
```

## 接口清单

以下为当前项目中主要使用的接口与实时通道，按功能分组。

### 系统监控 `monitor`
- `GET /api/v1/perf/monitor/server-info`：获取后端启动时间，用于连接历史校验
- `POST /api/v1/perf/monitor/connect`：建立 SSH 连接，返回 `connection_id`
- `POST /api/v1/perf/monitor/system-info`：获取 CPU / 磁盘 / 网络监控数据
- `POST /api/v1/perf/monitor/disconnect`：断开 SSH 连接

### FIO 测试 `fio`
- `POST /api/v1/perf/fio/validate-hosts`：验证主机 SSH 连接和 FIO 可用性
- `POST /api/v1/perf/fio/start-test`：启动 FIO 测试任务
- `POST /api/v1/perf/fio/stop-test`：停止 FIO 测试任务
- `GET /api/v1/perf/fio/test-status/<task_id>`：获取测试状态
- `WebSocket /api/v1/perf/fio/ws`：接收实时输出、统计数据和完成状态

### CosBench 配置生成
- `POST /api/v1/perf/cosbench/generate`：生成 CosBench 配置内容

### VDBench
- `POST /api/v1/perf/vdbench/generate`：生成 VDBench 配置
- `POST /api/v1/perf/vdbench/parse`：解析结果或配置内容

### 网段扫描 `ping`
- `POST /api/v1/network/ping/scan`：执行网段扫描

### 网络聚合 `bond`
- `POST /api/v1/network/bond/scan-hosts`：扫描或验证主机
- `POST /api/v1/network/bond/configure`：下发 Bond 配置
- `POST /api/v1/network/bond/check`：检查 Bond 状态

### 带宽测试 `iperf3`
- `POST /api/v1/network/iperf3/start`：启动测试
- `GET /api/v1/network/iperf3/results/<task_id>`：获取测试状态与结果
- `DELETE /api/v1/network/iperf3/cancel/<task_id>`：取消测试
- `POST /api/v1/network/iperf3/validate`：验证主机连接
- `WebSocket /api/v1/network/iperf3/ws`：实时测试通信

### Ansible 管理平台
- `POST /api/v1/system/ansible/validate`：验证主机连接
- `POST /api/v1/system/ansible/command`：批量执行命令或模块
- `POST /api/v1/system/ansible/file-transfer`：推送或拉取文件
- `POST /api/v1/system/ansible/playbook/validate`：校验 Playbook YAML
- `POST /api/v1/system/ansible/playbook/run`：执行 Playbook

### 系统初始化 `system_init`
- `POST /api/v1/system/system-init/validate`：验证主机连接
- `POST /api/v1/system/system-init/full-init`：执行完整初始化
- `POST /api/v1/system/system-init/modify-hostnames`：批量修改主机名
- `POST /api/v1/system/system-init/configure-hosts`：写入 hosts 配置
- `POST /api/v1/system/system-init/disable-firewall`：关闭防火墙
- `POST /api/v1/system/system-init/disable-selinux`：关闭 SELinux
- `POST /api/v1/system/system-init/create-users`：创建用户
- `POST /api/v1/system/system-init/setup-ssh`：配置免密或 SSH 相关设置

## 最近更新

### 2026-07-15（v2.0 当前版本）
- 修复网络聚合配置页面：验证主机成功后，Bond 配置区仍显示空态的问题。
- Bond 页面显示条件改为基于成功验证主机 `successfulHosts` / `hasConnectedHosts`，避免仅依赖 `connectedServers` 导致状态不同步。
- 统一 Bond 页面目标主机选择逻辑：全选、应用配置、刷新状态、清除 Bond 均复用成功验证主机列表。
- IP 地址输入校验与占位提示改为基于 `connectedHostCount`，兼容单主机与多主机场景。
- 已在 Ubuntu 22.04 / Python 3.10 / Node.js 24 环境重建虚拟环境并验证前端构建通过。
- 当前启动方式：后端 Daphne 监听 `0.0.0.0:6000`，前端 Vite 监听 `0.0.0.0:6500`。

### 2026-03
- 新增 Ansible 管理平台
- 首页模块布局调整：系统监控移入系统工具
- 删除持续网络测试与数据库连接工具模块
- 系统初始化支持 IP 段 / CIDR 输入与主机名前缀自动生成
- 所有 SSH 相关模块默认用户名统一为 `root`
- FIO 页面顶部统计卡片布局已调整为更均衡的 2x2 排布

## 说明

- 本项目优先复用 `frontend/src/common.css` 与 `frontend/src/apps/common/` 下的公共资源
- 当前系统监控模块仍使用 HTTP 轮询，不使用 WebSocket
- 若修改前端模块可用状态，刷新页面即可生效
