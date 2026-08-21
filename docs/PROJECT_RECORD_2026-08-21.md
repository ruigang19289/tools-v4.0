# Tools 项目操作记录

> 记录日期：2026-08-21
>
> 本文档用于记录 Tools 3.0 和 Tools 4.0 的功能变更、验证结果和发布信息。文档纳入 Git 版本管理，但不打包进 Docker 镜像。

## Tools 3.0

### 系统初始化文件部署

在系统初始化的完整初始化流程中增加标准文件部署功能。初始化目标主机时，自动将项目 `files/` 目录中的文件部署到目标主机：

| 源文件 | 目标路径 | 权限 |
| --- | --- | --- |
| `files/sdsos_bash_profile.sh` | `/etc/profile.d/sdsos_bash_profile.sh` | `0644` |
| `files/toprc` | `~/.toprc` | `0644` |
| `files/config` | `~/.ssh/config` | `0600` |

实现要点：

- 使用 SFTP 上传，先写入临时文件，再重命名到目标路径。
- 自动创建目标主机的 `~/.ssh` 目录并设置为 `0700`。
- 支持重复执行，目标文件会被更新。
- 支持密码认证和服务端挂载 SSH 私钥认证。
- 默认从项目内 `files/` 目录读取文件。
- 可通过 `TOOLS_INIT_FILES_DIR` 环境变量指定外部文件目录，适配容器和其他部署路径。
- 文件缺失或部署失败时，会在主机结果中记录错误。

### 版本与发布

- 前端界面版本：`3.2`
- GitHub 仓库：`https://github.com/ruigang19289/tools`
- 分支：`v3.0`
- 相关提交：`d2cf24c feat: enhance system initialization deployment`

### 验证

- 后端 Python 语法检查通过。
- `python3 manage.py check` 通过。
- Git 差异检查通过。
- 原有未提交修改已保留并随本次版本提交。

## Tools 4.0

### 独立仓库与分支

- GitHub 仓库：`https://github.com/ruigang19289/tools-v4.0`
- 分支：`v4.0`
- 前端界面版本：`4.0`

### 节点部署预检

在系统工具菜单下新增“节点预检”功能，用于存储节点部署前的准入检查。工具源码位于：

```text
tools/node_precheck/
```

支持的能力：

- BIOS 检查。
- 操作系统检查。
- CPU、NUMA、内存和硬件检查。
- NVMe、PCIe 链路和磁盘检查。
- 网卡、RDMA、RoCEv2 和网络检查。
- Dry Run。
- 节点信息采集。
- 磁盘性能测试。
- 网络性能测试。
- 稳态磁盘测试入口。
- 检查结果、原因、修复建议和报告文件输出。

页面入口：

```text
系统工具 -> 节点预检
```

后端接口：

```text
POST /api/v1/system/node-precheck/run
GET  /api/v1/system/node-precheck/artifact/<run_id>/<path>
```

安全和运行约束：

- 节点配置只写入本次执行的临时文件，执行结束后删除。
- 稳态磁盘测试属于破坏性操作，页面要求二次确认。
- 执行报告保存到 `data/node-precheck/<run_id>/`，该目录属于运行数据，不进入 Git。
- 可通过 `TOOLS_NODE_PRECHECK_DIR` 环境变量指定节点预检工具目录。

### 与硬件巡检的功能边界

- 硬件巡检：通用硬件信息采集、PCIe/NVMe/网卡/NUMA 信息展示和多节点对比。
- 节点预检：存储节点部署准入检查、PASS/WARN/FAIL/ERROR 判定和整改建议。
- 两个入口保持独立，后续可复用主机认证组件，并在节点预检结果中关联硬件详情。

### 真实节点验证

已使用节点 `10.3.11.61` 完成只读全量巡检验证：

- Ping 正常。
- SSH 密码认证成功。
- 远端主机名：`sds1`。
- 未执行整改。
- 未执行磁盘性能和稳态破坏性测试。
- 巡检结果：`PASS=15, WARN=5, FAIL=10, ERROR=11`。
- 报告运行编号：`b58b4335a30c40cfbb60f12f024de8b6`。

主要发现包括：根目录空间不足、超线程未关闭、NUMA Balancing 未关闭、NVMe NUMA 分布不均、RDMA 工具/版本不符合要求，以及已有 Ceph OSD 数据的 NVMe 不能直接格式化。

### 版本与发布

- 节点预检接入提交：`8f7b9c9 feat: add storage node precheck tool`
- 界面版本调整提交：`2033901 chore: set frontend version to 4.0`

### 验证

- 后端 Python 语法检查通过。
- `python3 manage.py check` 通过。
- 前端 `npm run build` 通过。
- Dry Run 接口实测通过。
- 真实节点只读巡检链路验证通过。

## 文档打包策略

本目录 `docs/` 仅用于项目记录和开发文档。为避免文档进入 Docker 镜像，项目 `.dockerignore` 已排除：

```text
docs/
```

源码、前端构建所需文件、初始化文件和节点预检工具不受该规则影响。
