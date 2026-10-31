# Docker 执行环境 - 技术文档

> 状态：方案设计（未开发） · 前置阅读：[产品文档](./step1_product.md)

## 1. 方案概览

### 1.1 核心思路

**不重写文件 / 命令工具，而是把 gtsp 搬进容器。** 现有 TSP driver 通过 stdio 连接宿主 gtsp 执行 `read_file` / `execute_bash` / `process_*`。「进入容器」= 断开宿主 gtsp，把同一个 `_client` 连到容器内的 gtsp。

```text
Agent turn
├─ create/delete/list ──► containerService（docker CLI）
├─ enter/exit ──────────► TspAgentDriver（切换 _client）
└─ TSP 工具 ────────────► 当前 _client：宿主 gtsp 或 容器内 gtsp
```

### 1.2 容器内 gtsp 的拉起形态

所有形态最终都走 `TSPClient.from_stdio(command)`，仅 `command` 与前置动作不同：

| 容器来源与状态 | 容器主进程（PID 1） | gtsp 拉起方式 | 退出方式 |
|---|---|---|---|
| **自建容器**（`create_container` 创建，初始 stopped） | gtsp（经 `--init`） | `docker start -a -i <id>` 直接拉起 | 关 stdin → gtsp 退出 → 容器天然停止（0.2s，不需调用 `docker stop`） |
| **外部 / 他人容器（必须处于 running）** | 容器原主进程 | `docker cp` 注入二进制 + `docker exec -i <id> <gtsp>` | 关 stdin → gtsp 退出，容器继续运行（不需调用 `docker stop`） |
| **外部 / 他人容器（已停止）** | — | **不支持直接进入**。报错提示「该容器已停止，请先执行 docker start 启动」 | — |
| **宿主环境** | — | 现有 `build_gtsp_command`，直接拉起宿主二进制 | — |

- **自建容器 `--init` 与自动退出机制（为什么不需 `docker stop`）**：
  - 自建容器使用 `--init` 参数启动，PID 1 为 Docker 内置的 `docker-init`（tini），`gtsp` 作为其主子进程运行；
  - **`--init` 不会阻碍容器自动退出**：tini 的默认行为是将主子进程（`gtsp`）的生命周期等同于容器生命周期。当驱动端关闭 stdio 时，`gtsp` 收到 EOF 退出；tini 捕获到主子进程退出事件后，**会立即以 `gtsp` 的退出码自身退出**，绝不会因容器内残留后台进程而挂起等待；
  - **Linux PID 1 内核机制保障**：在 Linux PID 命名空间中，一旦 PID 1（tini）退出，内核会立即向该命名空间内的所有其他存活进程广播 `SIGKILL` 并销毁命名空间。因此即便 Agent 在容器内后台启动了其他任务（如 `nohup`、编译守护进程等），只要 `gtsp` 退出，容器也必定在 0.1~0.2s 内自然停止转为 `exited` 状态。
- **外部容器简化**：外部容器进入前必须已在运行。若处于停止状态，系统不自动代为拉起（因为 Agent 本身可用宿主命令 `docker start` 启动它），启动后再进入按运行中容器处理。退出时仅关闭 exec 的 stdio，不影响容器主进程，同样不涉及 `docker stop`。

### 1.3 关键决策

| 决策 | 理由 |
|---|---|
| 进入容器 = 切换 TSP client | 工具语义零改动 |
| 单 client，切换时关闭旧环境 gtsp | 后台进程本就不该跨环境存活 |
| `image` 默认 `ubuntu:24.04`，只做参数默认值 | 不引入配置面 |
| `enter_container` 不校验属主、不挂载 | 产品要求；Docker 也无法给已有容器加 mount |
| `list_containers` 只返回 DB 记录 | 不枚举宿主其他容器 |
| 进入状态只存内存，重启一律回宿主 | 外部容器无法可靠恢复；避免「DB 说在容器、实际不在」 |
| 自建容器进入前已 running → 报错 `container_already_running` | 空闲 running 是异常残留，隐式 stop 会抹掉线索 |
| 删除正在使用的容器 → 报错 `container_in_use` | 删除不隐含环境切换 |
| 退出机制：关 stdin 等 gtsp 自动退出 | 实测 EOF 后 gtsp 立即退出（0.2s 内）；不引入级联 stop/kill |
| 只有 TSP driver 暴露容器工具 | Native / Claude SDK 的工具不经过 gtsp |

## 2. 数据模型与持久化

### 2.1 表 `agent_containers`（迁移 `0017_agent_containers.sql`）

只记录 `create_container` 创建的容器。直接使用容器的 Docker ID，不生成额外的系统 ID。外部容器不进 DB。

| 字段 | 类型 | 说明 |
|---|---|---|
| `agent_id` | `int` | 所属 Agent ID（建立索引） |
| `docker_id` | `str` | 唯一，Docker 容器原生 ID（对外接口展示前 12 位短 ID） |
| `container_name` | `str` | 唯一，容器名称（`agent-<name>-<index>`） |
| `image` | `str` | 创建时实际使用的镜像（默认 `ubuntu:24.04`） |
| `deleted` | `int` | 软删除标记（默认 `0`，删除后置为时间戳或 `1`），便于链路排查与执行回溯 |
| `created_at` | `int` | 创建时间戳 |

- **不存运行状态**：运行状态实时 `docker inspect`，避免缓存漂移；
- **不存当前进入状态**：执行环境只存在于内存中，重启后一律回到宿主；
- **架构 `arch`**：无需存库，创建或注入时通过 `docker inspect` 镜像/容器动态解析；
- **无需序号列 `seq`**：单 Agent 容器上限为 3，创建时直接从该 Agent 活跃容器中取 `{1, 2, 3}` 中未被使用的最小编号作为名称后缀；
- **软删除设计**：`delete_container` 执行 `docker rm -f` 后，不物理删除 DB 行，而是更新 `deleted = 1`；日常查询（列表、配额统计等）仅统计 `deleted == 0` 的活跃容器。
- **DAL `gtAgentContainerManager`**：提供 `create`、软删除 `mark_deleted`、`list_active_by_agent`、`count_active_by_agent`、按名 / docker_id 反查。

---

## 3. ContainerService 服务层设计

### 3.1 运行参数与常量（代码硬编码）

本版暂不支持用户配置文件，运行参数全部在代码中硬编码为常量：

| 常量 / 参数 | 硬编码值 | 说明 |
|---|---|---|
| `DEFAULT_CONTAINER_IMAGE` | `ubuntu:24.04` | 自建容器默认镜像 |
| `MAX_CONTAINERS_PER_AGENT` | `3` | 单 Agent 自建容器上限 |
| 容器网络 (`network`) | `bridge` | 默认开启宿主桥接网络 |
| `create_timeout_sec` | `60` | 创建容器超时时间 |
| `enter_timeout_sec` | `60` | 进入容器与 gtsp 握手超时时间 |
| `exit_timeout_sec` | `10` | 退出时等待 gtsp 结束超时时间 |
| `inspect_timeout_sec` | `3` | Docker inspect 状态查询超时（防阻塞） |
| `--init` | 恒开 | 由 docker-init（tini）回收孤儿僵尸进程 |
| gtsp 注入路径 | `/tmp/gtsp` | 容器内固定二进制注入路径 |

### 3.2 容器内 gtsp 二进制管理与注入

#### 二进制规格与分发
- **预置路径**：存放于静态资源目录 `assets/execute/gtsp/`（`gtsp-linux-amd64`、`gtsp-linux-arm64`），桌面打包时由 `scripts/togo_desktop.spec` 打入安装包。
- **静态编译**：通过 `CGO_ENABLED=0 go build -ldflags="-s -w"` 编译，无动态链接库依赖，兼容所有 Linux 发行版（含 Alpine 等 musl libc 镜像）。
- **架构匹配**：运行时通过 `docker inspect` 获取容器/镜像的架构（`Architecture`），按 `amd64` / `arm64` 选择匹配的二进制。

#### 统一注入流程（`docker cp`）
容器内固定使用 `/tmp/gtsp` 作为二进制路径，日志输出至 `/tmp/togospace/logs`。统一通过 `docker cp` 注入：

| 容器类型 | 注入时机 | 启动方式 |
|---|---|---|
| **自建容器** | `create_container` 成功后立即注入 | `docker create` 指定 `--entrypoint /tmp/gtsp`，进入时 `docker start -a -i` |
| **外部容器** | `enter_container` 建立连接前注入 | `docker exec -i <docker_id> /tmp/gtsp --mode stdio ...` |

- 注入后确保目标具备可执行权限（源文件保持 0755，必要时由容器内补齐权限）；
- 注入不依赖宿主机与 Docker Desktop 之间的目录共享（File sharing）配置。

### 3.3 模块结构与职责

- `containerService/core.py`：容器生命周期编排、配额管理、命名规则、目标元数据解析、二进制 `docker cp` 注入。
- `containerService/dockerCli.py`：Docker 路径自动探测（`which` → 常见默认安装路径）、argv 执行（禁止 `shell=True`）、超时控制与错误分类。

### 3.4 提供的核心接口

| 接口 | 说明 |
|---|---|
| `create_container(agent_id, image=None) -> ContainerInfo` | 校验配额（≤3）、生成命名与 label、执行 `docker create` 挂载目录、`docker cp` 注入 gtsp、写 DB、失败补偿清理 |
| `delete_container(agent_id, container_id: str) -> bool` | 校验不是当前使用中容器，执行 `docker rm -f` 并标记 DB 软删除 (`deleted=1`) |
| `delete_agent_containers(agent_id: int) -> int` | Agent 离职/改名时，批量强制删除该 Agent 的所有自建容器并标记软删除 |
| `delete_team_containers(team_id: int) -> int` | Team 删除/改名/工作目录迁移时，批量强制删除该 Team 下所有自建容器并标记软删除 |
| `list_agent_containers(agent_id) -> list[ContainerInfo]` | 查询该 Agent 未删除的自建容器并并行 inspect 实时状态 |
| `get_container_target(agent_id, container_id: str) -> ContainerTarget` | 校验 container_id，查验 DB / Docker 获取架构 arch 与 is_managed，组装 ContainerTarget |
| `prepare_container_gtsp(container: ContainerTarget)` | 执行 `docker cp` 将 Linux gtsp 注入到 `/tmp/gtsp` 并赋权（自建容器在 create 时注入，外部容器在 enter 前注入） |

### 3.5 容器创建与命名规范

- **命名**：`agent-<name>-<index>`（`index` 取 1..3 中未占用的最小编号），Agent 名非法字符（中文、空格等）回退为 `agent-<agent_id>-<index>`。
- **Label 标识**：打上 `com.togospace.agent_id`，便于运维通过 Docker CLI 追溯容器所属 Agent。
- **创建参数**：
  ```text
  docker create --name agent-<name>-<index>
    -i --init --entrypoint /tmp/gtsp
    --mount type=bind,src=<agent_workdir>,dst=/workspace/self
    --mount type=bind,src=<shared_workdir>,dst=/workspace/team_shared,readonly
    --env HOME=/tmp --network bridge --security-opt no-new-privileges
    <image> --mode stdio --log-path /tmp/togospace/logs
  ```
- **关键约束**：
  - 必须带 `-i`，绝不带 `-t`（保持原始流传输，不分配伪终端）；
  - `--entrypoint` 必须置于镜像名之前；显式传容器内可写的 `--log-path`；
  - 不传 `--workdir`（跟随镜像默认 `WORKDIR`）；创建后立即通过 `docker cp` 注入 `/tmp/gtsp`；
  - **开启 `--init` 的双重保障**：
    1. **回收孤儿僵尸进程**：Agent 执行命令或复杂脚本派生的子进程若先于孙子进程退出，孙子孤儿进程会被内核自动 reparent 到 PID 1（tini），由 tini 自动 `waitpid` 收割，防止 PID 资源耗尽；
    2. **不影响自动退出**：tini 默认将 `gtsp` 视为主进程，`gtsp` 退出时 tini 立即同步退出，触发内核清理所有残留进程并使容器转为 `exited` 停止状态。

### 3.6 错误码分类

统一返回 `{"success": false, "error_code": ..., "message": ...}`：
- Docker 环境类：`docker_cli_not_found`、`docker_daemon_unavailable`、`container_mount_denied`；
- 镜像类：`container_image_missing`（提示手动 `docker pull`）、`container_image_not_linux`、`container_arch_unsupported`；
- 容器状态类：`container_not_found`、`container_lost`、`container_already_running`、`container_in_use`、`container_stopped`（外部容器停止提示）；
- 执行类：`container_create_failed`、`container_enter_failed`、`container_not_executable`、`container_limit_reached`。

---

## 4. TspAgentDriver 执行环境管理

### 4.1 运行态变量（内存维护，不落库）

`TspAgentDriver` 维护当前执行环境，重启一律回到宿主：

| 变量 | 类型 | 说明 |
|---|---|---|
| `_exec_env` | `ExecEnv` | 当前执行环境：`HOST` 或 `CONTAINER`，默认 `HOST` |
| `_current_container` | `Optional[ContainerTarget]` | 当前连接的目标容器，宿主环境下为 `None` |
| `_client` | `Optional[TSPClient]` | 活跃 TSP client 实例（同一时刻只连宿主或容器之一） |

- `ExecEnv`：`host` / `container`。
- `ContainerState`（实时 inspect）：`created` / `running` / `stopped` / `missing` / `unknown`。
- **核心不变量**：自建容器稳态仅为 created / stopped；空闲时 running 视为异常。外部容器 running 属正常。

### 4.2 连接目标 `ContainerTarget`

纯粹的底层连接结构体，去除了与连接无关的展示字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `docker_id` | `str` | Docker 容器原生 ID（用于 CLI 参数） |
| `is_managed` | `bool` | True: 自建容器(形态 A)；False: 外部/他人容器(形态 B) |
| `arch` | `str` | 架构（`amd64` / `arm64`），用于外部容器注入匹配的 gtsp 二进制 |
| `owned_by_current_agent` | `bool` | 是否为当前 Agent 自己创建（仅用于丢失时决定是否删 DB 记录） |

### 4.3 Driver 连接控制接口

Driver 专注于维护底层的 stdio 连接与 TSP 工具加载，提供两个核心接口供上层编排调用：

#### 1. `disconnect() -> None`
- **动作**：若当前 `_client` 存在，调用 `await self._client.disconnect()` 关闭 stdio 连接，并置 `_client = None`；
- **清理工具**：清空 `_tsp_tools`；
- **环境收敛**：若原处于容器内，stdio 关闭后容器内 gtsp 收到 EOF 自然退出（自建容器随之停回 stopped，外部容器仅 exec 退出、主进程保持运行）；
- **重置状态**：将 `_exec_env` 重置为 `HOST`，`_current_container` 置为 `None`。

#### 2. `connect(env: ExecEnv = ExecEnv.HOST, container: Optional[ContainerTarget] = None) -> bool`
- **入参定义**：
  - `env`：目标环境枚举（`ExecEnv.HOST` 或 `ExecEnv.CONTAINER`），显式控制连接环境；
  - `container`：当 `env == ExecEnv.CONTAINER` 时必传，传入容器连接描述体；`env == ExecEnv.HOST` 时忽略；
- **命令构造**：
  - 若 `env == ExecEnv.HOST`：使用 `build_gtsp_command` 生成宿主机 gtsp 命令；
  - 若 `env == ExecEnv.CONTAINER`：使用 Driver 内部 `_build_container_command(container)` 生成容器启动命令（自建为 `docker start -a -i <docker_id>`，外部为 `docker exec -i <docker_id> /tmp/gtsp --mode stdio ...`）；
- **拉起与初始化**：
  - 通过 `client = await TSPClient.from_stdio(command, timeout_sec).start()` 建立 stdio 连接；
  - 执行 `await client.initialize(...)` 完成协议握手；
- **加载工具**：调用 `self._load_tsp_tools(client.tools)` 加载容器内/宿主工具，并在宿主 Driver 上重新注册；
- **更新状态**：更新 `_client = client`、`_exec_env = env` 与 `_current_container = container`；
- **返回值**：成功返回 `True`；异常（如超时或握手失败）捕获并返回 `False`。

#### 3. 重连与异常回落
- `_ensure_connected()`：按 `_exec_env` 分派，若在容器内断连，依据 `_current_container` 重新调用 `connect(ExecEnv.CONTAINER, container=self._current_container)`；若在宿主断连，调用 `connect(ExecEnv.HOST)`；
- `fallback_to_host(reason)`：先调用 `disconnect()`，再调用 `connect(ExecEnv.HOST)` 回退到宿主机；若容器丢失且 `owned_by_current_agent=True`，同步将 DB 记录标记软删除；
- 提示处理：共享目录只读提示（`read-only file system`）、镜像无 bash 提示（`executable file not found` + `bash`，只提示一次）。

---

## 5. 端到端执行流程串联

从 Agent 发起 Tool 调用开始，系统各层（`funcToolService`、`containerService`、`TspAgentDriver`）的协同执行逻辑如下：

### 5.1 进入容器流程（`enter_container(container_id)`）

```mermaid
sequenceDiagram
    autonumber
    actor Agent
    participant Tool as funcToolService
    participant CS as containerService
    participant Driver as TspAgentDriver
    participant Docker as Docker Daemon / CLI

    Agent->>Tool: enter_container(container_id)
    alt 当前已在目标容器
        Tool-->>Agent: 幂等返回成功
    end
    Tool->>CS: get_container_target(agent_id, container_id)
    CS->>Docker: 校验存在并获取容器架构 (arch)
    CS-->>Tool: 返回 ContainerTarget(docker_id, is_managed, arch)
    
    alt 外部容器处于 stopped
        Tool-->>Agent: 报错拒绝（提示先自行 docker start）
    else 自建容器处于 running
        Tool-->>Agent: 报错拒绝（提示容器异常残留）
    end

    alt 是外部容器 (not is_managed)
        Tool->>CS: prepare_container_gtsp(target)
        CS->>Docker: docker cp 注入 /tmp/gtsp 并赋予执行权限
    end

    Note over Tool,Driver: 环境切换：先断开，再显式连接到 CONTAINER
    Tool->>Driver: disconnect()
    Note over Driver,Docker: 关闭旧 client stdio，若前一个为自建容器则自然停止
    Tool->>Driver: connect(ExecEnv.CONTAINER, container=target)
    Driver->>Docker: 拉起 TSPClient.from_stdio(command)
    Driver->>Driver: client.initialize() 并重载工具列表
    
    alt 连接握手成功
        Tool->>Tool: 发布 AGENT_CONTAINER_STATE_CHANGED WebSocket 事件
        Tool-->>Agent: 返回成功（环境: container, 工作目录, 挂载提示）
    else 连接握手失败
        Tool->>Driver: connect(ExecEnv.HOST) 回落宿主
        Tool-->>Agent: 返回进入失败错误
    end
```

### 5.2 退出容器流程（`exit_to_host()`）

1. **Agent 发起**：调用 `exit_to_host()`；
2. **断开当前环境**：调用 `driver.disconnect()`，关闭当前容器的 stdio 连接；
   - 自建容器：内部 `gtsp` 收到 EOF 退出，Docker 自动将容器停回 `stopped`；
   - 外部容器：内部 `gtsp` 退出，`docker exec` 会话结束，外部容器原主进程保持 `running`；
3. **恢复宿主连接**：调用 `driver.connect(ExecEnv.HOST)`，重新拉起宿主 `gtsp` 并恢复宿主工具列表；
4. **发布事件与返回**：发布 WebSocket 环境变更事件，向 Agent 返回退出成功消息（提示宿主已就绪，`/workspace/self` 等路径已失效）。

### 5.3 容器创建流程（`create_container(image)`）

1. **Agent 发起**：调用 `create_container(image="...")`；
2. **配额与命名**：`containerService` 校验该 Agent 当前活跃自建容器数（未删除且 `< 3`），在 `{1, 2, 3}` 中选择未被占用的最小编号 `index`，生成容器名 `agent-<name>-<index>`；
3. **执行创建**：调用 `docker create` 绑定个人工作目录读写、团队共享目录只读，设置 `--entrypoint /tmp/gtsp`；
4. **立即注入**：创建完成后立即调用 `docker cp` 将对应架构的 Linux `gtsp` 二进制注入到容器内部 `/tmp/gtsp`；
5. **记录落库**：在 `agent_containers` 表中插入一条新记录（`agent_id`, `docker_id`, `container_name`, `image`, `deleted=0`, `created_at`）；
6. **返回结果**：返回 `container_id` 与提示信息，**当前执行环境保持在宿主不变**。

### 5.4 容器删除流程（`delete_container(container_id)`）

1. **Agent 发起**：调用 `delete_container(container_id="...")`；
2. **安全校验**：
   - 校验目标容器是否为当前正在使用的容器，若是则直接报错拒绝（提示必须先调用 `exit_to_host` 退出）；
   - 校验是否为当前 Agent 自己创建的容器（外部容器不支持由本系统删除）；
3. **执行删除**：调用 `docker rm -f <docker_id>` 强制移除容器实体；
4. **标记软删除**：更新数据库表 `agent_containers` 对应行的 `deleted = 1`；
5. **返回结果**：返回删除成功提示。

---

## 6. Agent 容器工具契约与参数规范

### 6.1 注册与权限控制

- **实现与注册**：统一在 `funcToolService/tools.py` 编写 5 个容器工具函数，在 `funcToolService/core.py` 中注册；
- **动态导入约定**：遵循 `tools.py` 中已有工具函数的既有项目模式，在函数体内动态导入 `from service import agentService, containerService`，并通过 `_context.agent_id` 获取当前 Agent 的 Driver 进行环境切换，避免顶层模块循环导入；
- **权限分类**：全部归入 `ToolCategory.EXECUTE`（角色无 Execute 权限则不可见）；
- **生效范围**：仅对 `TspAgentDriver` 开放（`get_tools(include_container_tools=False)` 控制），Native / Claude SDK 等其他 Driver 不暴露；
- **寻址原则**：**所有需要指定容器的工具，入参仅接受 12 位 Docker ID（`container_id`）**，不接受容器名称或模糊前缀，语义清晰且杜绝歧义。

### 6.2 工具参数与返回定义

#### 1. `create_container`（创建自建沙箱容器）

- **功能**：创建配置好工作目录挂载的沙箱容器。**仅创建，不切换执行环境**。
- **参数列表**：

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| `image` | `str` | 否 | `"ubuntu:24.04"` | Docker 镜像名称（如 `"python:3.11"`、`"ubuntu:24.04"`），必须为本地已存在的 Linux 镜像 |

- **返回字段**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `success` | `bool` | 是否创建成功 |
| `container_id` | `str` | 容器的 12 位短 ID |
| `container_name` | `str` | 容器名称（`agent-<name>-<1..3>`） |
| `image` | `str` | 实际使用的镜像名称 |
| `state` | `str` | 容器初始状态（固定为 `"created"`） |
| `mounts` | `dict` | 挂载映射（`/workspace/self` 读写，`/workspace/team_shared` 只读） |
| `message` | `str` | 提示说明（提醒当前仍处于宿主环境，如需使用需调用 `enter_container`） |

#### 2. `enter_container`（切换执行环境至指定容器）

- **功能**：将 Agent 执行环境切换进入指定的容器（自建容器或已在运行的外部容器）。
- **参数列表**：

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| `container_id` | `str` | 是 | — | 目标容器的 12 位 Docker ID |

- **返回字段**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `success` | `bool` | 是否进入成功 |
| `exec_env` | `str` | 切换后的执行环境（固定为 `"container"`） |
| `container_id` | `str` | 当前容器的 12 位 Docker ID |
| `container_name` | `str` | 容器名称 |
| `mounted` | `bool` | 是否挂载了个人工作目录（自建容器为 true，外部容器为 false） |
| `workdir` | `str` | 当前所在工作目录 |
| `message` | `str` | 提示说明（说明当前所在容器、工作目录、挂载读写性与退出后的容器生命周期表现） |

#### 3. `exit_to_host`（退出容器返回宿主机）

- **功能**：断开容器环境连接，切换回宿主机执行环境。
- **参数列表**：无参数。
- **返回字段**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `success` | `bool` | 是否退出成功 |
| `exec_env` | `str` | 切换后的执行环境（固定为 `"host"`） |
| `workdir` | `str` | 恢复后的宿主工作目录 |
| `message` | `str` | 提示说明（明确已恢复至宿主环境，`/workspace/self` 等容器路径已不可用） |

#### 4. `list_containers`（查看自建容器列表与当前环境）

- **功能**：查询当前 Agent 自己创建的容器列表及实时运行态，并返回当前执行环境。
- **参数列表**：无参数。
- **返回字段**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `success` | `bool` | 是否查询成功 |
| `docker_available` | `bool` | 本地 Docker 服务是否可用 |
| `exec_env` | `str` | 当前 Agent 执行环境（`"host"` 或 `"container"`） |
| `current_container_id` | `str \| null` | 当前若在容器内，为该容器的 12 位 Docker ID；宿主下为 null |
| `containers` | `list[object]` | 当前 Agent 的自建容器列表，每一项包含：`container_id`、`container_name`、`state`（`created`/`running`/`stopped`/`missing`）、`is_entered`、`image`、`created_at` |

#### 5. `delete_container`（删除指定自建容器）

- **功能**：删除指定的自建容器（执行 `docker rm -f` 并标记软删除）。
- **参数列表**：

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| `container_id` | `str` | 是 | — | 要删除的自建容器 12 位 Docker ID。当前正在使用的容器禁止删除 |

- **返回字段**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `success` | `bool` | 是否删除成功 |
| `deleted_container_id` | `str` | 被删除的容器 12 位 Docker ID |
| `message` | `str` | 删除成功提示说明 |

### 6.3 契约与 Schema 规范

- **严格 ID 传参**：`enter_container` 与 `delete_container` 参数严格命名为 `container_id`，校验为 12 位 16 进制字符串；
- **Docstring 规范**：面向 Agent 友好描述，不出现 `attach` / `exec` / `client` / `stdio` 等底层实现词；
- **Schema 快照与分类**：
  - `image` 默认值写在 Python 函数签名，不写入 tool schema 的 `default`（避免部分模型省略传参）；
  - 更新快照 `scripts/tool_schema_snapshot.py --update`；
  - `EXPECTED_TOOL_CATEGORIES` 工具总数由 37 调整为 42（EXECUTE 分类 4 → 9）。

## 7. 生命周期与清理

自建容器以 `gtsp` 作为主进程运行在交互式 stdio 模式下，**生命周期与底层 stdio 连接严格绑定**：
- 连接断开（正常退出、后端停机、进程崩溃、kill 等导致 stdin 收到 EOF），`gtsp` 在 0.2s 内自然退出，容器随之自动停回 `exited`（stopped）；
- 因此**后端启动与停机时无需主动扫描或批量停止容器**，零启动延迟，也不会阻塞停机。

| 时机 | 行为 |
|---|---|
| 后端启动 | 无需扫描或停止 Docker 容器，零等待直接就绪 |
| 后端停机 | `agentService` 销毁时关闭各 Driver 的 stdio 连接，自建容器自然停止退出 |
| Team 停用 / 清空 | `unload_team` 销毁各 Driver 断开 stdio 连接，自建容器自然停止退出 |
| 单 Agent 热更新 | 旧 Driver `shutdown` 断开 stdio，旧容器自然退出停回，新 Driver 起在宿主 |
| 后端崩溃 | 宿主进程退出导致 stdio 破裂，`gtsp` 收到 EOF 退出，Docker 自动将容器停回 |

**目录变更时的清理**：bind mount 在创建时固化路径，目录移动后旧容器失效，必须在移动前清理。

| 操作 | 处理 |
|---|---|
| Agent 改名 / 离职（`overwrite_team_agents`） | DB 提交后、删除目录前调用 `containerService.delete_agent_containers` |
| Agent 改名（`save_agent` 工具 → `rename_agent_workdir`） | 在锁内 `force_fallback_to_host` 后清理自建容器 |
| Team 改名 / 工作目录变更（目录实际移动时） | 移动目录前调用 `delete_team_containers` 清理自建容器 |
| Team 删除 | 销毁 runtime 后调用 `delete_team_containers` 清理自建容器 |
| 部门同步导致的离职 | 不删目录，也不清理容器 |

`delete_*_containers` 是 best-effort：先 `rm -f` 再标记 DB 软删除 (`deleted=1`)；`rm` 失败也标记软删除，并记录 warning；内部吞掉所有异常，不阻塞目录操作。

## 8. 接口与 Web

- **Agent 列表**：增加 `exec_env`、`current_container_id`、`container_count`（`count_map_by_agent_ids` 只查 DB）。未加载的 Agent 恒为 host。
- **Agent 详情**：额外增加 `containers`（`list_agent_containers` 实时查询），用 `ContainerInfo.model_dump(mode="json")`。
- **WebSocket**：新 topic `AGENT_CONTAINER_STATE_CHANGED`，事件名 `agent_container`，payload 为 `gt_agent`、`exec_env`、`current_container_id`。只由 driver 在环境实际变化时发布（进入、退出、回落、改名回落）。
- **活动记录**：`AgentActivityMeta` 增加 `exec_env` / `current_container_id` / `container_name`，开始时写入，结束时 patch 为最终环境。
- **前端**：`types.ts` / `api.ts` / `eventNormalizer.ts` / `runtimeStore.ts` 增加字段与事件；Agent 卡片显示容器徽标；活动条目显示环境徽标；属性面板增加「执行环境」区块与容器表；i18n 增加 `agent.execEnv.*` / `agent.containers.*` / `agent.containerState.*`。
- TUI 本次不改。

## 9. 安全

- 自建容器只挂个人目录、只读共享目录，gtsp 通过 `docker cp` 注入而不依赖宿主目录挂载；不挂 `docker.sock`，不使用 privileged，默认开启 `no-new-privileges`。
- LLM 唯一的创建自由度是 `image`，不影响挂载、网络、entrypoint 等参数；不自动 pull，避免被诱导拉取远端镜像。
- 所有 docker 命令走 argv，注入路径和参数均为常量；`container_id` 严格校验 12 位 hex。
- 只传 `HOME` 环境变量，API key 不进容器；日志与 stderr 脱敏、截断。
- `enter_container` 有意放宽（见产品文档的已知风险）。
- 容器只做环境隔离，不是强安全沙箱（默认 root、共享内核）。不可信代码应配置 `network=none`、资源限制，必要时使用 rootless Docker 或 VM。

## 10. 改动文件

| 范围 | 文件 |
|---|---|
| 常量 / 路径 | `constants.py`、`appPaths.py`（`get_gtsp_linux_binary_path`）、`util/logUtil.py`（`containerService` 日志拆分） |
| 模型 / DAL / 迁移 | `model/dbModel/gtAgentContainer.py`、`model/coreModel/containerModel.py`、`dal/db/gtAgentContainerManager.py`、`assets/migrate/0017_agent_containers.sql` |
| Service | `service/containerService/{__init__,core,dockerCli}.py`、`funcToolService/{core,tools}.py`、`agentService/toolRegistry.py` |
| Agent | `driver/tspDriver.py`、`driver/base.py`、`agent.py`、`agentService/core.py`、`agentTurnRunner.py`、`agentTaskConsumer.py`（透传 `shared_workdir`）、`agentActivityService.py` |
| 生命周期 | `backend_main.py`、`teamService.py` |
| Controller | `agentController.py`、`wsController.py` |
| 资源 / 打包 | `assets/execute/gtsp/gtsp-linux-{amd64,arm64}`、`scripts/togo_desktop.spec` |
| 前端 | `types.ts`、`api.ts`、`realtime/{eventNormalizer,runtimeStore}.ts`、`AgentListSection.vue`、`AgentActivityItem.vue`、`AgentPropertiesPanel.vue`、`locales/*.json` |

## 11. 测试

| 层 | 范围 |
|---|---|
| 单元 `tests/unit/service/containerService/` | CLI 路径解析；错误分类；创建 argv（`-i` 存在、`--entrypoint` 下标 < image 下标、无 `-t` / `--workdir` / `--read-only`、含 `--log-path` 与 `--init`）；中文名回退且长名不截断；image 参数（空值走默认、自定义值原样、非 linux / 架构不支持 / 缺失均不落库）；并发创建不超过 3；`get_container_target`（12 位 hex 格式校验、镜像名拒绝）；形态 A / B 命令构造与 distroless 兜底；二进制注入（docker cp 保留可执行权限）；工作目录继承；删除时 `container_in_use`；目录变更清理 |
| 单元 `test_tsp_driver_container.py` | 自建 stopped 进入；自建 running 报错且不调用 stop / start；外部 running 进入与退出；外部 stopped 报错拒绝；同容器幂等；A → B 切换；握手失败恢复并回落；超时；关 stdin 容器自动退出；退出必回宿主；丢失且为自建容器时标记软删除；Docker 不可用保留记录；单 client 切换；Cancelled 清理；启动不恢复；只读 / 无 bash 提示（仅一次） |
| 集成 | fake `docker` 脚本（JSON 状态），`start -a -i` / `exec -i` 挂真实宿主 gtsp 子进程，走 `run_tool_call` 跑完整链路 |
| 生命周期 | 迁移 0017 幂等；`backend_main` 启停顺序 |
| schema | 快照、参数顺序、分类契约；`get_tools()` 默认不含容器工具 |
| 真实 Docker e2e（`TOGOSPACE_TEST_DOCKER=1`） | 覆盖产品验收清单：形态 A 关 stdin 停止、形态 B 保持运行、漏 `-i` 负向用例、alpine 无 bash 负向用例、`windows/nanoserver` 拒绝、外部容器进出状态、重启回宿主等 |

## 12. 风险与待定

| 风险 | 对策 |
|---|---|
| 镜像无 bash，看似正常但 `execute_bash` 失败 | 按需提示一次；不静默回退 `sh` |
| Docker Desktop 未共享目录 | `container_mount_denied` 并提示添加 File sharing |
| Linux 宿主上 root 容器写出的文件属主为 root | 已知限制，本版不做 `--user` |
| 将来支持非 root 用户 | `--log-path` 需指向可写 bind 目录 |
| 纯 Go DNS resolver 行为差异 | e2e 验证短域名解析，必要时用 `GODEBUG=netdns=cgo` |
| 升级后旧容器使用的是旧版 gtsp | 握手失败或版本不一致时重新注入或提示删除重建 |
| 同一 `STORAGE_ROOT` 运行多个后端 | 假设单实例 |
| Windows / WSL2、Podman 未验证 | 只针对 macOS + Docker Desktop 验证过 |

待定：Web 容器表布局；残留容器一键清理入口；TUI 展示；gtsp 结构化错误码（替代文本匹配，属协议层改动，本版不做）。

## 13. 实施顺序

1. ~~gtsp 静态化 + 进入 / 退出行为实测~~（已完成）
2. 常量 / 配置 / 迁移 / model / DAL
3. dockerCli + containerService（fake docker 单测）
4. TSP driver 环境切换与进入 / 退出
5. 容器工具注册 + schema 快照（同一 commit）
6. 生命周期与目录变更清理
7. controller / WS / 前端
8. 打包（Linux gtsp 随包分发，验证 file sharing）
9. 真实 Docker e2e + 产品验收
