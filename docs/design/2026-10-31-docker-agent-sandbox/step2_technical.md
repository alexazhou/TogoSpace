# Docker 沙箱执行能力 - 技术文档

> 状态：方案设计（未开发）
> 前置阅读：[产品文档](./step1_product.md)
> 设计日期：2026-10-31
> 本版口径：5 工具（管理 3 + 访问 2），默认镜像 `ubuntu:24.04`，gtsp 作为容器主进程

## 1. 方案概览

### 1.1 与产品文档的对应关系

产品文档定义了**两块能力**，本文档按此分层实现：

| 产品能力 | 章节 | 工具 | 技术落点 |
|---|---|---|---|
| 能力一：容器管理 | 产品 §2 | `create_container` / `delete_container` / `list_containers` | `containerService` + `agent_containers` 表 |
| 能力二：容器访问 | 产品 §3 | `enter_container` / `exit_to_host` | `TspAgentDriver` 双 client + 容器内 gtsp |
| 其他特性 | 产品 §4 | — | 开关、异常分类、WS / 活动记录 |
| 依赖安装落点 | 产品 §6 | — | bind mount `/workspace/self` 双向、`/workspace/team_shared` 只读 |

产品与技术的分工边界：

- 产品回答「Agent 看到什么、能做什么、做不到时提示什么」；
- 本文档回答「这些东西在代码里怎么落地、DB 怎么存、进程怎么起、失败怎么收」。

### 1.2 核心思路

**不重写文件 / 命令工具，而是把现有的 gtsp 工具服务搬进容器。**

现有 TSP driver 通过 stdio 拉起宿主机上的 `gtsp`，所有 `read_file` / `write_file` / `execute_bash` / `process_*` 都由这个进程执行。「进入容器」本质上就是：把 driver 的**活跃 TSP client** 从「宿主 gtsp」换成「容器里的 gtsp」。

```text
Agent turn（后端进程）
├─ 本地工具 create/delete/list ──► containerService（docker CLI）
├─ 本地工具 enter/exit ─────────► TspAgentDriver.enter_container / exit_to_host
│                                  ├─ containerService：docker start / stop
│                                  └─ 切换 active TSPClient
└─ TSP 工具 read_file/execute_bash/... ──► 当前 active TSPClient
                                            ├─ host：宿主 gtsp（原有进程，常驻）
                                            └─ container：容器内的 gtsp
```

**关键点：容器内 gtsp 有两种宿主形态，取决于容器是谁起的。**

| 容器来源 | 容器主进程（PID 1） | 进入方式 | 退出方式 |
|---|---|---|---|
| `create_container` 创建 | **gtsp 自己** | `docker start -a -i <id>` | 关 stdin → gtsp 退出 → 容器退出 |
| 别人创建 / Agent 自己 `docker run` | 容器原有主进程 | `docker start <id>` + `docker exec -i <id> <gtsp>` | 关 stdin → gtsp 退出；若容器是本次拉起的则 `docker stop` |

为什么自己创建的容器要让 gtsp 当 PID 1：

- 镜像千千万，没人能保证某个镜像的 ENTRYPOINT / CMD 起来之后容器还在运行、还能接受 `docker exec`；但我们**确定知道 gtsp 怎么跑**，让它当 PID 1 就把「容器活着」这件事变成可控的。
- gtsp 退出 = 容器退出，一个进程的生命周期同时是容器生命周期，天然满足「退出后容器停回停止状态」，不需要额外协调。
- 不需要往镜像里塞常驻进程（`sleep infinity` 之类），不污染镜像语义。

无论哪种形态，代码里都收敛成**同一个 `TSPClient.from_stdio(...)`，只是 `command` 不同**（宿主 / `start -a -i` / `run --entrypoint` / `exec`），driver 的切换、重连、失败处理只有一套。

### 1.3 关键决策

| 编号 | 决策 | 理由 |
|------|------|------|
| D1 | 进入容器 = 切换活跃 TSP client | 工具语义零改动，不为容器再实现一套 file/exec 工具 |
| D2 | 自己创建的容器里 gtsp 作为 PID 1 | 镜像 ENTRYPOINT 不可控；gtsp 的生命周期 = 容器生命周期，退出即停容器 |
| D3 | 别人的容器用 `docker exec -i` 跑 gtsp | 不动容器主进程，才能做到「退出后容器继续运行」 |
| D4 | 所有形态复用 `TSPClient.from_stdio`，只换 command | 切换 / 重连 / 失败处理只有一套代码 |
| D5 | `create_container` 不开放 `image` 参数，内置 `ubuntu:24.04` | 默认镜像不做成配置项，避免一开始就引入配置面（产品 §2.3） |
| D6 | `enter_container` 接受任意已存在的容器，不校验属主、不做挂载 | 产品 §3「访问不产生归属、不做挂载」；能力与配额解耦 |
| D7 | 自动挂载只在 `create_container` 创建时发生 | Docker 无法修改已有容器的 mount；也符合产品语义 |
| D8 | `list_containers` 只返回自己创建的容器 | 产品 §4.4：不枚举宿主其他容器，但允许指名进入 |
| D9 | `enter_container` / `exit_to_host` 不落库，进入状态不做持久化恢复 | 外部容器无法可靠恢复（可能被删）；后端重启一律回宿主，永不残留「声称在容器」的悬空状态 |
| D10 | 自己创建的容器在进入前若是 running，先 stop 再 `start -a -i` | 空闲时还 running 说明是残留主进程；不复用 attach（会与已有 gtsp 争抢同一条 stdio 流） |
| D11 | 删除当前正在使用的容器直接报错，要求先 `exit_to_host` | 产品 §2.4；不在删除流程里隐式踢人，避免语义意外 |
| D12 | 退出走三级兜底：关 stdin → `docker stop` → `docker kill` + warning | 无法保证 daemon 在 stdin 关闭时一定会终止容器进程 |
| D13 | DB 只存 `create_container` 的记录，运行态只存内存 | 配额、列表、外部删除识别需要持久化；进入状态不需要 |
| D14 | 非 TSP driver 不暴露容器工具 | Native / Claude SDK 的工具在各自进程内执行，无法用同一机制切换 |

### 1.4 改动范围

| 层 | 改动 |
|----|------|
| 常量/枚举 | 新增 `ExecEnv`、`ContainerState`、容器 label 常量、`MAX_CONTAINERS_PER_AGENT = 3`、`DEFAULT_CONTAINER_IMAGE = "ubuntu:24.04"` |
| 配置 | `SettingConfig` 新增 `container` 配置段；模板与配置文档同步 |
| 数据模型 | 新增 `GtAgentContainer`（表 `agent_containers`） |
| 迁移 | 新增 `assets/migrate/0017_agent_containers.sql` |
| DAL | 新增 `gtAgentContainerManager` |
| Service | 新增 `service/containerService/`（Docker CLI 适配 + 容器生命周期 + 容器内 gtsp 命令构造） |
| 工具 | 新增 5 个本地工具；TSP driver 专属可见；归入 `EXECUTE` 类别 |
| Driver | `TspAgentDriver` 增加双 client、进入 / 退出、容器丢失检测与回落 |
| Agent | `Agent` 暴露 `agent_workdir` / `shared_workdir` / `exec_env` / `current_container_id` / `supports_container_env`，转发 `enter_container` / `exit_to_host` / `force_fallback_to_host` |
| 生命周期 | `backend_main` 启停接入；`teamService.stop_team_runtime` 停止容器 |
| 接口 | Agent 列表 / 详情 payload 增加执行环境字段；详情附带容器列表 |
| Web | WS 事件、runtime store、Agent 卡片徽标、属性面板容器表、i18n |
| 打包 | `scripts/togo_desktop.spec` 不再排除 `gtsp-linux-*`（容器内需要 Linux 版 gtsp） |
| gtsp 构建 | `gtsp-linux-amd64` 改静态链接（`CGO_ENABLED=0`）——**已完成**（TSP `8c5e131`，任务 #105）；否则 `ubuntu:20.04` / alpine 等镜像直接跑不起来 |
| 测试 | 单元 / 集成 / 生命周期 / 工具 schema 快照 / 可选真实 Docker e2e |

---

## 2. 总体架构

### 2.1 分层与模块

遵循四层架构，新增模块均不反向依赖上层：

```text
controller
  └─ agentController（读执行环境与容器列表）
service
  ├─ containerService/                    新
  │   ├─ core.py            容器生命周期、状态、命名、上限
  │   ├─ dockerCli.py       docker 命令执行与错误分类
  │   ├─ gtspInContainer.py 容器内 gtsp 命令构造（三种形态）
  │   └─ __init__.py
  ├─ funcToolService/containerTools.py    新：5 个工具的实现
  ├─ funcToolService/tools.py             重新导出工具（快照脚本按此查找）
  ├─ funcToolService/core.py              注册工具、按 driver 过滤
  └─ agentService/driver/tspDriver.py     双 client、进入 / 退出、回落
dal
  └─ db/gtAgentContainerManager.py        新
model
  ├─ dbModel/gtAgentContainer.py          新
  └─ coreModel/containerModel.py          新：ContainerInfo / ContainerListResult
util / appPaths
  └─ get_gtsp_linux_binary_path(arch)     新
```

约束：

- `containerService` 只依赖 `dal` / `model` / `util` / `appPaths` / `constants`，**不 import `agentService` / `funcToolService`**；
- `containerTools.py` 对 `agentService` 使用函数内局部 import（与 `tools.py` 现有做法一致），避免循环依赖；
- `containerService` 模块导入期不得读取配置、连接数据库或执行 docker，保证工具 schema 快照测试可独立导入。

### 2.2 运行态模型

每个 TSP Agent 的 driver 维护两份 TSP client：

```python
self._host_client: TSPClient | None       # 宿主 gtsp，启动即建立，常驻
self._container_client: TSPClient | None  # 当前容器内的 gtsp，仅进入容器时存在
self._client: TSPClient | None            # 活跃 client（保留原名字）：
                                          #   宿主环境 == _host_client
                                          #   容器环境 == _container_client
self._exec_env: ExecEnv                   # HOST / CONTAINER
self._current_container: ContainerTarget | None
self._entered_started_by_us: bool         # 本次进入是否由我们拉起容器（决定退出时是否停回）
```

`_client` 保留现有名字，`_execute_tsp_tool`、`_is_client_connected` 等现有逻辑只面对「活跃 client」，改动面最小。

`ContainerTarget` 是**运行态**目标描述（不进 DB）：

```python
class ContainerTarget(BaseModel):
    container_id: str       # 本系统容器 = 12 位 hex；外部容器 = "docker:" + docker_id 前 12 位
    container_name: str     # docker inspect 解析出的容器名
    docker_id: str          # docker 容器完整 ID
    managed: bool           # True = create_container 创建（DB 有记录）；False = 外部容器
    image: str
    arch: str               # amd64 / arm64
```

只有 `managed=True` 的目标才对应 `agent_containers` 一行；外部容器只在内存里存在，退出即丢弃。

**执行环境不做持久化恢复**（D9）：后端重启一律回到宿主。原因：

- 产品文档没有「重启后自动回到容器」的要求，反而在 §8 要求「后端崩溃 / 重启后不残留运行中的容器」；
- 外部容器随时可能被删除或改建，恢复必然是不可靠的；
- 不恢复就不存在「DB 声称在容器、实际不在」的悬空状态，省掉一整类一致性问题。

### 2.3 状态机

Docker 侧容器状态（`ContainerState`，对外小写）：

| 状态 | 含义 | 来源 |
|------|------|------|
| `created` | 已创建、未启动 | docker `created` |
| `running` | 正在运行 | docker `running` / `restarting` |
| `stopped` | 已停止 | docker `exited` / `dead` |
| `missing` | Docker 中已不存在 | inspect 返回 No such object |
| `unknown` | Docker 不可用，状态未知 | daemon 连接失败 |

Agent 执行环境（`ExecEnv`）：`host` / `container`，只由 driver 运行态决定。

合法迁移（以「自己创建的容器」为例）：

```text
（host，无当前容器）
  create_container ──► created（仍 host，不切换）
  enter_container ──► running + current（container）
  exit_to_host ──► stopped（host）
  再次 enter_container ──► running（容器层内容保留）
  delete_container（非当前）──► 记录 + 容器删除（host 不变）
  delete_container（当前）──► 报错 container_in_use，要求先 exit_to_host
  外部 docker rm ──► missing；下次工具调用检测到后回落 host + 删除记录
```

外部容器（`managed=False`）：

```text
  enter_container（R0=running）──► 容器保持 running（我们只在里面 exec 一个 gtsp）
  enter_container（R0=stopped）──► docker start → running，记录 started_by_us=True
  exit_to_host（started_by_us=False）──► 容器继续 running
  exit_to_host（started_by_us=True）──► docker stop，停回 stopped
```

### 2.4 与现有 driver 的关系

| driver | 是否支持容器环境 | 说明 |
|--------|------------------|------|
| `TSP` | 是 | 文件 / 命令工具都由 gtsp 执行，可直接替换 client |
| `NATIVE` | 否 | 只注册本地工具，没有文件 / 命令执行能力 |
| `CLAUDE_SDK` | 否 | 内置工具在 SDK 子进程内执行，不经过 gtsp，无法用同一机制切换 |

因此：

- 只有 `TspAgentDriver._local_tools` 包含容器工具；
- Native / Claude SDK 的 `funcToolService.get_tools()` 调用保持默认参数，看不到容器工具；
- 工具实现内部仍校验 driver 能力，作为防御（`container_unsupported_driver`）。

---

## 3. 数据模型与迁移

### 3.1 `agent_containers`

只记录 `create_container` 创建的容器。外部容器不落库（D9）。

```python
# src/model/dbModel/gtAgentContainer.py
class GtAgentContainer(DbModelBase):
    agent_id: int = peewee.IntegerField(index=True)
    container_id: str = peewee.CharField(unique=True)      # 对外 ID，12 位 hex
    docker_id: str = peewee.CharField(default="")          # docker 容器 ID
    container_name: str = peewee.CharField(unique=True)    # docker 容器名
    seq: int = peewee.IntegerField(default=1)              # 序号，用于 agent-<name>-<seq>
    image: str = peewee.CharField(default="")
    arch: str = peewee.CharField(default="")               # amd64 / arm64

    class Meta:
        table_name = "agent_containers"
        indexes = (
            (("agent_id", "created_at"), False),
        )
```

字段口径：

- `container_id`：后端生成的对外 ID（`secrets.token_hex(6)`），LLM / API / WS 只看到它，不暴露 `docker_id`；
- `docker_id` / `container_name`：后端内部寻址用，均为创建时 `docker create` 的结果；
- `seq`：容器序号，决定 `agent-<agent名>-<序号>` 里的数字，删除后序号不回收（避免名字复用导致 Agent 混淆）；
- `arch`：创建时从镜像 `Os/Architecture` 解析，用于选容器内 gtsp 二进制；
- **不存运行状态**（running/stopped），状态实时 `docker inspect`，避免缓存漂移；
- **不存 `is_current`**：执行环境只在内存（D9），DB 不需要「当前容器」概念；
- `created_at` / `updated_at` 由 `DbModelBase` 自动维护。

### 3.2 迁移 `0017_agent_containers.sql`

```sql
CREATE TABLE IF NOT EXISTS agent_containers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id INTEGER NOT NULL,
    container_id VARCHAR(16) NOT NULL UNIQUE,
    docker_id VARCHAR(64) NOT NULL DEFAULT '',
    container_name VARCHAR(63) NOT NULL UNIQUE,
    seq INTEGER NOT NULL DEFAULT 1,
    image VARCHAR(255) NOT NULL DEFAULT '',
    arch VARCHAR(16) NOT NULL DEFAULT '',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_agent_containers_agent
    ON agent_containers(agent_id, seq);
```

没有部分唯一索引（不存在 `is_current`）。迁移由 `src/db.py` 在 ORM 绑定前执行（现有机制），`.sql` 文件按文件名顺序执行，可重复运行。

### 3.3 DAL：`gtAgentContainerManager`

```python
async def create(record: GtAgentContainer) -> GtAgentContainer
async def list_by_agent(agent_id: int) -> list[GtAgentContainer]
async def list_by_agent_ids(agent_ids: list[int]) -> list[GtAgentContainer]
async def get_by_container_id(agent_id: int, container_id: str) -> GtAgentContainer | None
async def get_by_container_name(container_name: str) -> GtAgentContainer | None
async def get_by_docker_id(docker_id: str) -> GtAgentContainer | None
async def count_by_agent(agent_id: int) -> int
async def next_seq(agent_id: int) -> int
async def delete(agent_id: int, container_id: str) -> int
async def delete_by_agent_ids(agent_ids: list[int]) -> int
async def delete_by_docker_ids(docker_ids: list[str]) -> int
```

`next_seq` 取 `max(seq) + 1`（无记录时 1），在 `agent_operation` 锁内调用，保证并发创建不会撞名字。

`get_by_container_name` / `get_by_docker_id` 用于识别「某个 docker 容器是不是本系统创建的」，是 `enter_container` 区分 `managed` 的依据。

### 3.4 不变量

1. 同一 Agent 的 `agent_containers` 行数 ≤ 3（应用层 + 每 Agent 锁保证，见 §5.14）；
2. 同一 Agent 同时最多一个容器处于 running（自己创建的容器在退出后会停回，见 §5.12）；
3. 所有按 `container_id` 的操作都必须同时带 `agent_id` 查询，防止越权操作其他 Agent 的容器；
4. `container_name` 全局唯一（DAL + docker 双重约束）；
5. 运行态 `_current_container` 与 `_exec_env` 必须同时在切换成功后才更新，不允许出现「环境是容器但目标为空」。

---

## 4. 配置

### 4.1 `SettingConfig.container`

```python
# src/util/configUtil/configTypes.py
class ContainerConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    docker_binary: str = ""          # 空 = 自动探测
    network: Literal["bridge", "none"] = "bridge"
    create_timeout_sec: int = 60     # docker create 上限
    enter_timeout_sec: int = 60      # 进入容器（start + gtsp 握手）上限
    exit_timeout_sec: int = 15       # 退出（关 stdin + stop）上限
    stop_timeout_sec: int = 10       # 传给 docker stop -t
    inspect_timeout_sec: int = 3     # 单次 inspect
    memory_limit: str = ""           # 如 "4g"；空 = 不限制
    cpus: str = ""                   # 如 "2"；空 = 不限制


class SettingConfig(BaseModel):
    # ...现有字段...
    container: ContainerConfig = Field(default_factory=ContainerConfig)
```

### 4.2 默认值与理由

| 配置 | 默认 | 理由 |
|------|------|------|
| `enabled` | `true` | 容器工具默认可用；Docker 缺失时工具返回可读错误，不影响宿主能力 |
| `docker_binary` | 空（自动探测） | GUI 进程 PATH 常常找不到 Docker Desktop（§5.2） |
| `network` | `bridge` | 默认有网；敏感任务可设 `none` |
| `create_timeout_sec` | 60 | `docker create` 正常在秒级；超时视为异常并清理 |
| `enter_timeout_sec` | 60 | `docker start` + 容器内 gtsp 握手；首次进入还可能带 `apt` 之类的慢启动 |
| `exit_timeout_sec` | 15 | 关 stdin + `docker stop` + 兜底 kill 的总预算 |
| `stop_timeout_sec` | 10 | 传给 `docker stop -t`；超时后 `docker kill` |
| `inspect_timeout_sec` | 3 | 状态查询不能让详情接口被 Docker 拖死 |
| `memory_limit` / `cpus` | 空 | 不改变默认行为；需要时再限 |

**不做成配置项的项**（有意硬编码）：

| 项 | 值 | 理由 |
|---|---|---|
| `image` | `ubuntu:24.04` | 产品 §2.3 明确「默认镜像不做成系统配置项」。gtsp 静态化后镜像选择已不受 glibc 约束，选它是因为：主流基础镜像中兼容性最宽松（glibc 2.39），体积 78MB 与 debian:bookworm-slim 只差 3MB |
| 单 Agent 容器上限 | 3 | 产品硬约束 |
| `--init` | 恒开 | 接管僵尸进程回收 |
| gtsp 挂载路径 | `/opt/togospace/gtsp` | 常量 |
| 工作目录 | `/workspace/self` | 常量 |

`image` 想换怎么办：Agent 用 `execute_bash` 自己 `docker run` 起容器，再 `enter_container` 进去。镜像的灵活性由这条路提供（产品 §2.3）。

### 4.3 配置模板与文档

- `assets/config_template.json` 增加 `container` 段（含默认值与注释性说明）；
- `assets/docs/setting.README.md` 中英文两节都补 `container` 字段表；
- `tests/config/setting.json` 不强制添加，缺省即默认配置；
- 修改配置需要重启后端生效（现有配置加载机制，不做热更新）；
- `containerService.is_enabled()` 是唯一读取开关的入口：配置未初始化时返回 `False`（保证工具 schema / 单元测试在无 AppConfig 时可导入），后端启动后按真实配置返回；`TspAgentDriver` 构造时据此决定是否把容器工具加入 `_local_tools`。

### 4.4 路径新增

容器内 gtsp 需要从宿主可共享的路径挂载，不能直接用 `_MEIPASS` / `.app` 内的资源路径（Docker Desktop 不一定共享 `/Applications`、临时目录）。因此新增：

```text
STORAGE_ROOT/
└── bin/
    ├── gtsp-linux-amd64
    └── gtsp-linux-arm64
```

- 首次创建容器时，从 `assets/execute/gtsp/` 复制对应架构的二进制到 `STORAGE_ROOT/bin/`，`chmod 0755`；
- 自己创建的容器挂载 `STORAGE_ROOT/bin/gtsp-linux-<arch>:/opt/togospace/gtsp:ro`（只读）；
- 外部容器**不做挂载**（产品 §3.2），gtsp 二进制通过「先把文件写进容器再执行」的方式带进去，见 §5.10；
- `dev_storage_root/` 已在 `.gitignore` 中，无需额外处理；
- 该目录是运行时产物，不属于用户数据，删除后下次创建自动重建。

---
## 5. ContainerService

### 5.1 模块职责

`src/service/containerService/core.py`（与现有 service 一致，使用 `serviceUtil.service_start_wrapper` / `service_stop_wrapper` 和 `ServiceStatusInfo(name="containerService")`）：

| 能力 | 说明 |
|------|------|
| `startup` / `shutdown` | 生命周期挂接；启动时按 owner label best-effort 清理遗留 running 容器；停机时停止本实例管理的 running 容器（与 `enabled` 无关） |
| `create_container(agent_id, agent_workdir, shared_workdir)` | 校验上限、Docker；`docker create`；写 DB；失败补偿删除 |
| `list_containers(agent_id)` | 返回 DB 记录 + 实时状态（`ContainerInfo`），**只有自己创建的** |
| `get_container(agent_id, container_id)` | 按归属查询单条记录 |
| `resolve_target(agent_id, ref)` | 把 `container_id` 或容器名解析成 `ContainerTarget`（区分 managed / 外部），见 §5.11 |
| `inspect_container(record_or_target)` | 单条容器的实时状态（`ContainerState`，含 `missing` / `unknown`） |
| `start_container(target)` | 幂等 `docker start` 并等待 running；不存在则抛 `container_lost` |
| `stop_container(target)` | `docker stop`，超时 `docker kill`；不存在视为成功 |
| `kill_container(target)` | 退出兜底的 `docker kill`（§5.12） |
| `delete_container(agent_id, container_id)` | `docker rm -f` + 删除 DB 行；不存在视为成功 |
| `delete_agent_containers(agent_ids)` | Agent 改名（含 `save_agent` 工具路径）/ 离职时清理容器 |
| `delete_team_containers(team_id)` | Team 改名 / 删除时清理该 Team 全部 Agent 的容器 |
| `prune_missing(agent_id)` | 检测并删除 Docker 中已不存在的记录，用于上限自愈 |
| `describe_containers(agent_id)` | 状态查询（工具 / 详情接口共用） |
| `stop_team_containers(team_id)` | Team 运行时卸载时停止该 Team 全部 running 容器 |
| `count_map_by_agent_ids(agent_ids)` | 供 controller 批量取每个 Agent 的容器数量（只查 DB，不碰 Docker） |
| `agent_operation(agent_id)` | 返回按 Agent 的异步上下文锁，供创建 / 进入 / 退出 / 删除互斥（§5.14） |

`src/service/containerService/dockerCli.py`：

- 解析 docker 可执行文件路径；
- 以 argv 方式执行命令（**不使用 `shell=True`**）；
- 统一超时、stderr 截断、错误分类；
- 所有命令返回 `DockerResult(returncode, stdout, stderr)`。

`src/service/containerService/gtspInContainer.py`：

- 构造三种形态的容器内 gtsp 启动命令（§5.10）；
- 构造外部容器的 gtsp 注入脚本；
- 命令全部是常量拼接，不接收任何 LLM 输入。

### 5.2 Docker CLI 路径解析

GUI 形态（Tauri 桌面）拿到的 `PATH` 往往只有 `/usr/bin:/bin:/usr/sbin:/sbin`，找不到 Docker Desktop 安装的 CLI。解析顺序：

1. 配置 `container.docker_binary`（显式指定优先）；
2. `shutil.which("docker")`；
3. macOS 常见位置：
   - `/usr/local/bin/docker`
   - `/opt/homebrew/bin/docker`
   - `/Applications/Docker.app/Contents/Resources/bin/docker`
   - `~/.docker/bin/docker`
4. Windows 常见位置：
   - `%ProgramFiles%\Docker\Docker\resources\bin\docker.exe`
   - `%ProgramFiles%\Docker\Docker\resources\bin`
5. Linux：`/usr/bin/docker`、`/usr/local/bin/docker`。

解析结果按进程缓存；解析失败时每次调用重新尝试（用户可能刚安装 Docker）。所有 docker 子进程使用解析出的绝对路径，不依赖后端进程的 `PATH`。

### 5.3 Docker 可用性与错误分类

不单独常驻探测 daemon；在执行具体操作（`inspect` / `create` / `start` / `rm`）时根据 stderr 分类。分类表：

| error_code | 触发 | 典型 stderr |
|------------|------|-------------|
| `docker_cli_not_found` | 找不到 docker 可执行文件 | `FileNotFoundError` |
| `docker_daemon_unavailable` | daemon 未运行 / 无法连接 | `Cannot connect to the Docker daemon`、`Is the docker daemon running` |
| `container_image_missing` | 默认镜像本地不存在 | `No such image`、`pull access denied` |
| `container_image_not_linux` | 镜像 OS 不是 linux | image inspect `.Os != linux` |
| `container_mount_denied` | Docker Desktop 未共享该路径 | `mounts denied`、`is not shared from the host` |
| `container_not_found` | 记录的容器不存在 / 不属于该 Agent | DB 查询为空 |
| `container_lost` | Docker 中容器已不存在 | inspect `No such object` |
| `container_start_failed` | `docker start` 失败 | 其他非零返回 |
| `container_create_failed` | `docker create` 的其他失败 | 参数非法、daemon 拒绝创建等 |
| `container_name_conflict` | 容器名已被占用 | `Conflict. The container name ... is already in use` |
| `container_enter_failed` | 容器内 gtsp 启动 / 握手失败 | attach / exec 失败、initialize 超时、`exec format error` |
| `container_in_use` | 删除正在使用的容器 | 应用层（产品 §2.4） |
| `container_not_executable` | 容器内无法执行 gtsp | `Permission denied`、`not found`、架构不匹配 |
| `container_delete_failed` | `docker rm` 失败（非 No such container） | daemon 不可用等 |
| `container_create_timeout` | `docker create` 超时 | asyncio timeout |
| `container_limit_reached` | 已有 3 个 | 应用层 |
| `container_unsupported_driver` | 非 TSP Agent | 应用层 |
| `container_disabled` | `enabled=false` | 应用层 |
| `container_config_invalid` | 配置值非法（network / 资源限制格式） | 应用层 |
| `container_arch_unsupported` | 镜像架构不是 amd64 / arm64 | 应用层 |
| `container_binary_missing` | assets 中缺少对应架构的 Linux gtsp | `ensure_gtsp_binary` 找不到源文件 |
| `invalid_arguments` | 参数组合 / 格式非法 | 应用层 |

错误信息原则：

- 第一句说人话（发生了什么），第二句给可执行建议（怎么恢复）；
- 保留截断后的 docker stderr（≤500 字符）便于排障；
- 不把 docker 环境变量、后端配置中的密钥写进消息或日志。

对应产品 §4.3 的「五种情况各有独立且可读的提示」：

| 产品场景 | error_code |
|---|---|
| Docker 未运行 | `docker_daemon_unavailable` |
| 镜像缺失 | `container_image_missing` |
| 容器已被外部删除 | `container_lost` |
| 进入超时 | `container_enter_failed` + 超时时长 |
| 容器不可执行 | `container_not_executable` |

示例：

```text
Docker 未运行：无法连接 Docker daemon。请启动 Docker Desktop 后重试。
默认镜像 ubuntu:24.04 本地不存在。请先在宿主机执行 `docker pull ubuntu:24.04`。
进入容器超时（60 秒）。容器已停回进入前的状态，你的个人目录文件未受影响。
容器 abc123 已不存在（可能被外部删除）。你的个人目录文件仍在宿主上，未受影响；已切回宿主环境。
```

### 5.4 创建容器

`create_container` 的完整顺序：

1. 检查 `container.enabled`，否则 `container_disabled`；校验配置值（`network`、`memory_limit`、`cpus` 格式），非法 → `container_config_invalid`；
2. 调用方（工具层）已持有每 Agent 锁（§5.14），本方法自身不重复加锁；
3. 统计该 Agent 的 DB 行数；若 `>= 3`，先对全部记录做一次 `prune_missing`（外部删除的容器不再占用名额）；Docker 不可用时不做删除，直接返回 `docker_daemon_unavailable`；再次计数，仍 `>= 3` 则 `container_limit_reached`，消息含当前数量与删除建议（对应产品 §5「容器数量达到上限」场景）；
4. 解析 docker CLI；`docker image inspect --format '{{.Os}} {{.Architecture}}' ubuntu:24.04`：
   - CLI 缺失 / daemon 不可用 / 镜像缺失分别返回对应错误；
   - `Os != linux` → `container_image_not_linux`；
   - `Architecture` 映射为 `amd64` / `arm64`，否则 `container_arch_unsupported`；
   - **不自动 pull**：镜像缺失时明确提示用户手动 `docker pull`（产品不把自动拉取放进范围）；
5. `ensure_gtsp_binary(arch)` 从 assets 复制 Linux gtsp 到 `STORAGE_ROOT/bin/`；源文件缺失 → `container_binary_missing`；
6. 生成 `container_id = secrets.token_hex(6)`、`seq = next_seq(agent_id)`、容器名 `agent-<agent名>-<seq>`（§5.5）；`container_id` 撞唯一键时重试（最多 3 次）；`docker create` 报容器名冲突时，检查同名容器的 owner label，属于本实例则 `rm -f` 后重试一次，否则返回 `container_name_conflict`；
7. 执行 `docker create`（见下）；
8. 写 `agent_containers` 行；
9. 写库失败时补偿 `docker rm -f`，再抛出原错误；
10. 返回 `ContainerInfo`（state=created）。

创建命令（argv 形式）：

```text
<docker> create
  --name agent-<agent名>-<seq>
  --label com.togospace.owner=<owner12>
  --label com.togospace.agent_id=<agent_id>
  --label com.togospace.container_id=<container_id>
  --init
  --entrypoint /opt/togospace/gtsp
  --mount type=bind,source=<agent_workdir>,target=/workspace/self
  --mount type=bind,source=<shared_workdir>,target=/workspace/team_shared,readonly
  --mount type=bind,source=<STORAGE_ROOT>/bin/gtsp-linux-<arch>,target=/opt/togospace/gtsp,readonly
  --workdir /workspace/self
  --env HOME=/tmp
  --network <bridge|none>
  [--security-opt no-new-privileges]
  [--memory <memory_limit>]
  [--cpus <cpus>]
  ubuntu:24.04
  --mode stdio
  --workdir /workspace/self
  --log-path /tmp/togospace/logs
```

说明：

- **`--entrypoint /opt/togospace/gtsp` 是必须的**：镜像自带的 ENTRYPOINT（如 `bash`、`node`）会吞掉后面的 gtsp 参数，导致容器实际跑的是镜像自己的进程。显式指定后，gtsp 就是 PID 1（D2）。
- 后面的 `--mode stdio ...` 是传给 gtsp 的参数，不是传给镜像的；
- **不给 `-t`**：`docker create` 的 `-t`（`--tty`）会让 daemon 创建 TTY，后续 `docker start -a -i` 再叠一层 TTY 会污染 stdio（§6.4）；
- `HOME=/tmp` 避免非 root 用户没有可写 home（默认镜像用户是 root，这里只是兜底）；
- 只挂载两个目录 + 只读二进制，不挂 `docker.sock`，不挂其他 Agent 目录；
- `--init` 接管僵尸进程回收；
- 路径通过单一 argv 传递，不对路径做 shell 拼接；路径含逗号时 `--mount` 会解析失败，创建前给出 `container_config_invalid` 提示；
- `agent_workdir` / `shared_workdir` 先 `os.path.realpath` 归一化再 `os.makedirs`（Docker 要求 mount source 为绝对路径；realpath 避免符号链接导致 Docker Desktop 共享路径判断失效）；
- 镜像确认是一次性成本：`ubuntu:24.04` 首次创建前需要用户 `docker pull`，之后命中本地镜像。

创建超时处理：

- `asyncio.wait_for(communicate(), create_timeout_sec)`；
- 超时后 kill 子进程，再 best-effort 执行 `docker rm -f <container_name>`（CLI 被杀不代表 daemon 侧操作一定没完成）；
- 返回 `container_create_timeout`，不写 DB 行。

### 5.5 容器命名、归属与隔离

```text
容器名   = agent-<agent名>-<seq>          例：agent-小马哥-1
owner12  = sha256(os.path.realpath(STORAGE_ROOT))[:12]
labels:
  com.togospace.owner=<owner12>
  com.togospace.agent_id=<agent_id>
  com.togospace.container_id=<container_id>
```

命名规则要点：

- 序号从 1 开始，同一 Agent 内单调递增，**删除后不回收**。这样 `agent-小马哥-1` 永远指同一个容器，不会因为删除重建让 Agent 或用户在日志里认错；
- Agent 名称不做转义：名称本身已由 `workspaceUtil.validate_dir_name` 保证可作为跨平台目录名（不含 `/`、控制字符、Windows 保留名等），直接拼进容器名是安全的；
- `<agent名>` 长度受限时（`validate_dir_name` 允许的上限），容器名总长仍 < 63（docker 限制），超出时截断 Agent 名部分并保留 `-<seq>` 后缀。

labels 用途：

- 同一台 Docker daemon 上可能有多个 TogoSpace 实例（开发版 + 打包版、不同 `STORAGE_ROOT`），用 `owner` 隔离；
- 停机清理、孤儿识别时按 owner label 过滤，**禁止**使用 `docker ps -a` 全量扫描后无条件删除；
- `com.togospace.container_id` 让后续「这个 docker 容器是不是我们的」判断只需一次 inspect，不必反查 DB。

`container_name` 也可作为 `enter_container` 的入参（产品 §3：目标必须是已存在的容器，`container_id` / 容器名都行）。

### 5.6 gtsp 二进制 staging 与架构

`appPaths` 新增：

```python
def get_gtsp_linux_binary_path(arch: str) -> str:
    """arch: amd64 | arm64；返回 assets/execute/gtsp/gtsp-linux-<arch>。"""
```

`containerService.ensure_gtsp_binary(arch)`：

1. 目标路径 `STORAGE_ROOT/bin/gtsp-linux-<arch>`；
2. 目标不存在，或与 assets 中的源文件内容不一致（比较大小 + sha256）时，先复制到同目录临时文件（`shutil.copy2` + `chmod 0755`），再 `os.replace` 原子替换目标；
3. 内容一致则直接复用；原子替换保证已运行容器继续使用挂载时的旧 inode，新建容器使用新二进制；
4. 返回目标路径，供 `--mount` 与 `docker exec` 使用。

应用升级后，升级前创建的容器仍挂载旧版 gtsp。若新旧 gtsp 协议不兼容，该容器进入时会握手失败并回落宿主；错误信息提示 `delete_container` 后重新创建。新建容器始终使用升级后的二进制。

架构来源：**镜像的 `.Architecture`**，不是宿主架构。Docker Desktop 在 Apple Silicon 上默认运行 arm64 镜像；如果用户显式拉了 amd64 镜像，Docker 也可能用 Rosetta / QEMU 运行，gtsp 必须跟随镜像架构。

**静态链接是硬前提**（本方案依赖它，不再是「已知限制」）：

| 二进制 | 链接方式 | 说明 |
|---|---|---|
| `gtsp-linux-amd64` | 静态链接（已改造） | 原先是动态链接，要求 glibc ≥ 2.34，导致 `ubuntu:20.04`（2.31）、`debian:bullseye`（2.31）、alpine（musl）全部跑不起来。已在 TSP `8c5e131` 用 `CGO_ENABLED=0` 改为静态链接 |
| `gtsp-linux-arm64` | 静态链接 | 原本已是静态 |

glibc 对照（改造前实测）：

| 镜像 | glibc | 静态化前 | 静态化后 |
|---|---|---|---|
| `ubuntu:24.04` | 2.39 | ✅ | ✅ |
| `ubuntu:22.04` | 2.35 | ✅ | ✅ |
| `debian:bookworm-slim` | 2.36 | ✅ | ✅ |
| `ubuntu:20.04` | 2.31 | ❌ | ✅ |
| `debian:bullseye` | 2.31 | ❌ | ✅ |
| `alpine:3.19` | musl | ❌ | ✅ |

构建侧根因：CI（`ubuntu-latest`，默认 `CGO_ENABLED=1`）与 `gtsp/script/build.sh` 都未设 `CGO_ENABLED=0`，Go 的 `net` / `os/user` 走 cgo 路径产生动态链接。静态化后 Go 使用纯 Go 的 resolver，DNS 解析行为有细微差异（见 §13 风险）。

**注意：静态化后镜像选择不再受 glibc 约束。** 默认镜像选 `ubuntu:24.04` 的理由（§4.2）是「主流基础镜像中兼容性最宽松、体积代价小」，而不是「别的镜像跑不了」——alpine、debian、ubuntu 各版本现在都能跑。Agent 若想用别的镜像，走 `docker run` + `enter_container` 这条路即可（产品 §2.3）。

已知限制：

- 不支持 Windows 容器镜像（`Os != linux` 直接拒绝）；
- 镜像架构只支持 amd64 / arm64，其他架构（如 `386`、`riscv64`）返回 `container_arch_unsupported`。

### 5.7 启动 / 停止 / 删除

**启动**（`start_container`）：幂等。

```text
docker inspect --format '{{.State.Status}}' <docker_id>
  ├─ No such object → container_lost
  ├─ running / restarting → 直接返回
  └─ created / exited / dead → docker start <docker_id> → 轮询 inspect until running（最多 5 次 × 200ms）
```

**停止**（`stop_container`）：

```text
docker inspect（missing → 视为已停止，返回）
docker stop -t <stop_timeout_sec> <docker_id>
  ├─ 超时 / 返回后仍 running → docker kill <docker_id>
  └─ 其他错误 → 记录 warning，返回 best-effort 结果（不阻塞退出流程）
```

**删除**（`delete_container`）：

1. 按 `(agent_id, container_id)` 查记录，不存在 → `container_not_found`；
2. 调用方（工具层）保证「不是当前正在使用的容器」（§8.4）；
3. `docker rm -f <docker_id>`：
   - 成功 → 删除 DB 行；
   - stderr 含 `No such container` → 视为成功，删除 DB 行；
   - daemon 不可用 / 其他错误 → 不删除 DB 行，返回 `container_delete_failed`（状态查询仍能看到它）；
4. 返回删除结果。

### 5.8 状态查询

`describe_containers(agent_id)`：

1. `list_by_agent(agent_id)` 取 DB 行（不按 label 扫描，只认自己创建的）；
2. 对每行并行执行 `docker inspect --format '{{.State.Status}}'`（`asyncio.gather`，单次 3 秒超时）：
   - created → `created`；running/restarting → `running`；exited/dead → `stopped`；
   - No such object → `missing`；
   - daemon 不可用 → 本轮所有行 `unknown`，并附带可读错误；
3. 返回 `ContainerListResult`：

```python
class ContainerInfo(BaseModel):
    container_id: str
    container_name: str        # agent-<agent名>-<seq>
    state: ContainerState
    is_entered: bool           # 是否是当前正在使用的容器（运行态注入）
    image: str
    created_at: datetime


class ContainerListResult(BaseModel):
    docker_available: bool
    containers: list[ContainerInfo]
    message: str = ""
```

`is_entered` 由工具层用 driver 运行态填充，`containerService` 本身不知道谁是当前容器（不持久化，D9）。

状态查询最多 3 次 docker 调用，并行执行；整体 timeout 3 秒，避免详情接口被 Docker 拖死。Docker 不可用时所有行快速返回 `unknown`，不做重试。没有容器记录时不做 Docker 探测（没有可操作对象），`docker_available=true`。

### 5.9 停机与 Team 卸载

`containerService.shutdown()`：

- 查询本实例 owner label 下所有 running 容器（`docker ps -q --filter label=com.togospace.owner=<owner12> --filter status=running`）；
- 并行 `docker stop`（总超时 15 秒），失败只记日志；
- 不修改 DB；
- Docker 不可用时直接跳过，不阻塞后端退出；
- 即使 `container.enabled=false` 也执行，避免关闭功能后遗留运行中的容器。

`containerService.startup()` 的清理同理：按 owner label 停止所有 running 容器，不修改 DB；`docker ps` 探测超时 5 秒，Docker 不可用时跳过，不影响后端启动。

这条 sweep 覆盖了「后端崩溃留下 running 容器」——包括自己创建的容器（gtsp 主进程可能还活着）和外部容器（若我们曾把它拉起但进程崩了）。

`teamService.stop_team_runtime(team_id)` 在 `agentService.unload_team` 之后调用 `containerService.stop_team_containers(team_id)`：

- 取该 Team 全部 Agent 的容器记录，停止 running 的（执行 `docker stop`）；
- 保留记录与容器，Team 重新启用时 Agent 可再次 `enter_container`；
- 覆盖停用、Team 热更新、清空数据、删除 Team 等统一入口。

### 5.10 容器内 gtsp 的三种启动形态

这是本方案的技术核心。三种形态收敛到同一个 `TSPClient.from_stdio(command, timeout)`，只是 `command` 不同。

**形态 A：自己创建的容器（gtsp 是 PID 1）**

```python
def build_managed_command(target: ContainerTarget) -> list[str]:
    """自己创建的容器：gtsp 已经是 PID 1，用 start -a -i 接管它的 stdio。

    调用前已保证容器处于 stopped / created 状态（running 的情况由调用方先 stop）。
    """
    return [resolve_docker_binary(), "start", "-a", "-i", target.docker_id]
```

统一为 `docker start -a -i <docker_id>`，无论当前状态是 created / exited / running，都在调用前先 `stop`（如果 running）再 `start -a -i`。为什么不复用它已经在跑的 gtsp（`docker attach`）：见本节末尾。

**必须同时给 `-a` 和 `-i`，绝不能给 `-t`**：

| 参数 | 作用 | 给不给 |
|---|---|---|
| `-a`（`--attach`） | 把容器 stdout / stderr 接到当前进程 | 必须给，否则收不到 gtsp 输出 |
| `-i`（`--interactive`） | 把当前进程 stdin 接到容器 | 必须给，否则 gtsp 立刻 EOF 退出 |
| `-t`（`--tty`） | 分配 TTY | **绝不**。TTY 会插入行缓冲与终端控制字符，污染 JSON-RPC 帧 |

**形态 B：别人创建 / Agent 自己 `docker run` 的容器（原有主进程）**

```python
async def prepare_external_gtsp(target: ContainerTarget) -> list[str]:
    """外部容器：先把 gtsp 拷进容器，再用 exec -i 另起一个。

    两步：
    1. docker cp <宿主二进制> <docker_id>:/tmp/togospace/gtsp   （不依赖容器内有 shell）
    2. docker exec -i <docker_id> /tmp/togospace/gtsp --mode stdio ...  （直接 exec 二进制）
    """
    docker = resolve_docker_binary()
    await docker_cli.run([docker, "exec", target.docker_id, "mkdir", "-p", "/tmp/togospace"])
    await docker_cli.run([docker, "cp", host_binary, f"{target.docker_id}:/tmp/togospace/gtsp"])
    await docker_cli.run([docker, "exec", target.docker_id, "chmod", "0755", "/tmp/togospace/gtsp"])
    return [
        docker, "exec", "-i", target.docker_id,
        "/tmp/togospace/gtsp",
        "--mode", "stdio",
        "--workdir", "/tmp/togospace/work",
        "--log-path", "/tmp/togospace/logs",
    ]
```

外部容器**没有挂载**（产品 §3.2），所以 gtsp 二进制不能靠 bind mount 进去。用 `docker cp` 在进入前把二进制放进容器，再 `docker exec -i` 直接执行这个二进制：

- **不依赖容器内有 `base64` / `curl` / `bash`**，连 `sh` 都不需要（`exec` 直接跑二进制）；
- `docker cp` 不要求容器 running（stopped 容器也能 cp），但我们仍先 `start` —— 因为接下来要 `exec`，而 `docker exec` 对已停止容器会被 Docker 拒绝（`Container <id> is not running`）；
- 二进制落在 `/tmp/togospace/`，容器重建后消失，下次进入重新拷；
- 三个准备命令（`mkdir` / `cp` / `chmod`）都是常量 argv，不含任何 LLM 输入。

工作目录用 `/tmp/togospace/work`（外部容器没有 `/workspace/self` 映射）。工具在容器内的相对路径以它为准，绝对路径容器内自行解析。

极端镜像（distroless，无 `/bin/sh`）不影响本方案：`docker cp` 与 `docker exec <binary>` 都不走 shell。但 distroless 里可能没有 `/tmp` 的写权限或没有 `mkdir` 可执行文件——此时 `container_not_executable` 明确报错，不做兜底。

外部容器的运行状态处理（产品 §3.1）：

```text
进入前 inspect：
  ├─ running → 直接 docker exec -i（started_by_us = False）
  ├─ created / exited / dead → docker start（幂等）→ 等待 running → docker exec -i（started_by_us = True）
  └─ missing → container_lost
退出时：
  ├─ started_by_us = False → 只关 stdin，容器继续 running
  └─ started_by_us = True → 关 stdin → docker stop（停回 stopped）
```

**形态 C：宿主（原有逻辑，不变）**

```python
def build_host_command(raw_command, workdir) -> list[str]:
    """现有 build_gtsp_command，一字不改。"""
```

三者的共同点：

```python
client = TSPClient.from_stdio(command, timeout_sec)
await client.connect()
await asyncio.wait_for(client.initialize(...), enter_timeout_sec)
```

**为什么形态 A 不用 `docker attach`**：

- `attach` 只能接**已经 running** 容器的 stdio。对 created / exited 状态完全用不上，而这两种状态是常态（每次退出后容器都是 stopped）。
- 对 running 状态，`attach` 接的是**当前**那个 gtsp 进程的 stdio。但那个 gtsp 的 stdio 已经连在上一轮 `start -a -i` 的管道上——那个管道已经随着上一轮退出而关闭，gtsp 早就因此退出了。空闲时容器还 running 说明有异常残留（例如 gtsp 卡死没响应 EOF），此时正确做法是停掉重来，而不是把一条可能已经半死进程的 stdio 接过来。
- 所以形态 A 统一「先 stop（若 running）→ `start -a -i`」，不保留 `attach` 分支。

对照形态 B：外部容器的主进程不是 gtsp，我们每次 `exec -i` 起的是**全新**的 gtsp 进程，stdio 独占，不存在「接旧管道」的问题。

### 5.11 `enter_container` 的目标解析

`resolve_target(agent_id, ref)` 是「能力一」和「能力二」的交汇点，也是「能进 ≠ 能看见」的实现位置。

```text
输入 ref（container_id 或容器名）
1. 若是 12 位 hex → 按 (agent_id, container_id) 查 DB
   ├─ 命中 → managed=True，直接用记录的 docker_id
   └─ 未命中 → 当作 docker 容器 ID 前缀，走 2
2. 用 docker 查询：先按容器名精确匹配，再按 ID 前缀匹配
   docker inspect --format '{{.Id}}|{{.Name}}|{{.Config.Image}}|{{.Config.Labels}}' <ref>
   ├─ No such object → container_not_found（提示「容器不存在，可用 docker ps 查看」）
   └─ 命中：
      a. 若 labels 含 com.togospace.container_id → 反查 DB，managed=True
      b. 否则 managed=False（外部容器）
3. 解析镜像架构：docker inspect .Image → image inspect .Architecture
4. 返回 ContainerTarget
```

关键行为：

- **不校验属主**：别人的容器、手工起的容器、运维起的容器都能进（产品 §3）；
- **不做挂载**：外部容器不注入任何 bind mount，这是进入时就必须提示用户的（§8.3 返回体的 `message`）；
- **只接受容器，不接受镜像**：`ref` 传 `ubuntu:24.04` 时，`docker inspect` 会返回 No such object → `container_not_found`，兜底消息里追加「如果想用某个镜像，请先在宿主机上执行 `docker run` 起一个容器」（对应产品 §8 的验收项）；
- **此时 external 容器的 `container_id`**：内部用 `"docker:" + docker_id[:12]` 作为运行态标识，避免与我们的 12 位 hex 撞号。

### 5.12 退出的三级兜底

这是本方案**唯一未经真实验证**的行为（本机 docker daemon 当时未运行）。设计上按最保守假设写。

**问题**：`docker start -a -i` / `docker exec -i` 的 stdin 是我们这侧管道的写端。关闭写端后，容器进程能否感知 EOF 取决于 daemon 与容器进程之间的那段管道。gtsp 实测在 stdin EOF 时会打印 `stdin closed (EOF), server exiting` 并 exit 0 —— 但这只证明了「gtsp 收到 EOF 会退出」，**不能证明「我们关管道 → daemon 一定把 EOF 传进容器」**。

**因此退出流程按三级兜底设计**：

```text
exit_to_host(target):
1. 优雅关闭容器 client：client.shutdown()（TSP 协议层 shutdown，让 gtsp 自己退出）
   └─ 超时 / 失败 → client.disconnect()（关 stdin + 回收本地 docker CLI 子进程）
2. 等待容器状态变化：轮询 inspect，最多 exit_timeout_sec，每 200ms 一次
   ├─ managed（自己创建的）           → 期望 stopped
   ├─ 外部，started_by_us = True      → 期望 stopped
   └─ 外部，started_by_us = False     → 期望仍 running（我们只等自己的 gtsp 退出，不去碰容器状态）
3. 期望 stopped 但仍是 running → docker stop -t <stop_timeout_sec>
4. 仍不停 → docker kill，并记 warning（含 container_name，便于人工介入）
5. 无论停没停成，运行态一律切回 HOST（不把 Agent 留在悬空状态）
```

第 5 步是本设计的核心取舍：**退出是尽力而为，回宿主是必须成功**。即使容器没停掉，Agent 也必须能继续在宿主工作；残留的 running 容器由下次后端启动的 owner label sweep 收敛（§5.9）。

同理，`enter_container` 失败时（握手超时、`exec format error` 等）也走同一套停止逻辑，把容器恢复到进入前的状态，再回落宿主。

**待实测确认项**（实现阶段必须补）：

| 场景 | 期望 | 若不成立 |
|---|---|---|
| 形态 A：关 stdin → 容器是否 stopped | stopped | 依赖第 3 级 `docker stop`；功能可用但退出慢 15 秒 |
| 形态 B：关 stdin → 容器是否保持 running | running | 会误停别人的容器，需改为「不等待状态」+ 显式不 stop |
| 形态 B（started_by_us）：关 stdin → gtsp 退出 | 退出 | 依赖第 3 级 stop |
| `docker stop` 能否终止 PID 1 是 gtsp 的容器 | 能（gtsp 无 SIGTERM 忽略逻辑，实测会 `Received signal terminated, cleaning up processes...` 后退出） | 走第 4 级 kill |

验证方法（实现时先做的第一件事）：

```bash
docker run --rm -i --entrypoint /opt/togospace/gtsp ubuntu:24.04 --mode stdio
# 手动敲 initialize 帧，然后 Ctrl-D 关 stdin，观察容器是否退出
docker ps -a --filter name=agent-    # 确认没有残留 running
```

### 5.13 上限与并发

- `MAX_CONTAINERS_PER_AGENT = 3`，定义在 `constants.py`；
- **只算 `create_container` 创建的容器**（产品 §3.2）：外部容器不进 DB，天然不占配额，也不出现在 `list_containers`；
- `containerService` 提供 `agent_operation(agent_id)` 异步上下文锁（内部 `dict[agent_id, asyncio.Lock]` 按需创建），**公开方法本身不隐式加锁**，避免嵌套调用死锁；
- 锁的持有者与范围：

| 路径 | 锁范围 |
|---|---|
| `create_container` 工具 | 查记录 → prune → docker create → DB insert 全程 |
| `delete_container` 工具 | 重新查记录 → 校验不是当前容器 → docker rm → 删行 |
| `enter_container` 工具 | 解析目标 → start → 建 client → 切运行态 全程 |
| `exit_to_host` 工具 | 停容器 → 切回宿主 全程 |
| `list_containers` 工具 | **不加锁**（只读） |
| `TspAgentDriver._execute_tsp_tool` | 「检查 / 重连 + call_tool」全程，保证容器丢失检测不与 enter/exit/delete 交叉 |
| Team 停机 / 启动 sweep | 不抢锁。此时 scheduler 已停、Agent 已卸载，不存在并发 turn |

- `_ensure_connected()` 自身不获取 Agent 锁，只依赖调用方已持有；这样锁顺序始终是 `agent_operation → _connect_lock`；
- 不同 Agent 使用不同锁，可并行；锁字典规模 = 活跃 Agent 数（≤ 数百个，可接受）；
- 创建流程锁内 `prune_missing` → count → docker create → DB insert，并发创建不会超过 3。

### 5.14 外部删除的识别与收敛

外部删除（用户在终端 `docker rm -f`、别的工具清理、Docker Desktop 界面删除）可能在三个时机被发现：

| 时机 | 检测方式 | 处理 |
|---|---|---|
| 任一 TSP 工具调用 | client 进程退出 / 连接断开 → inspect → missing | `_handle_container_lost` |
| `list_containers` 查询 | inspect 返回 No such object | 返回 `state=missing`，**不自动删记录**（Agent 自己决定） |
| `create_container` 达到上限 | `prune_missing` | 自动删记录，释放名额 |
| 后端启动 sweep | `docker ps` 按 owner 过滤 | 只管停，不管删（DB 记录保留） |

`_handle_container_lost(target)`（仅对 managed 容器删记录）：

```text
1. force_fallback_to_host()（幂等；可能当前已是宿主）
2. 若 managed=True → containerService.delete_container(agent_id, container_id)，No such container 视为成功
3. 返回可读消息
```

消息（对应产品 §5「容器被外部清理」场景）：

```text
容器 abc123 已不存在（可能被外部删除）。
你的个人目录文件仍在宿主上，未受影响。
请调用 create_container 重新创建，再 enter_container 进入。
```

外部容器（managed=False）被删时只回落 + 不删记录（本来就没记录）。

---
## 6. 进入与退出

### 6.1 `TspAgentDriver` 状态

```python
class TspAgentDriver(AgentDriver):
    def __init__(...):
        ...
        self._host_client: TSPClient | None = None
        self._container_client: TSPClient | None = None
        self._client: TSPClient | None = None          # 活跃 client（保留原名）
        self._exec_env: ExecEnv = ExecEnv.HOST
        self._current_container: ContainerTarget | None = None
        self._entered_started_by_us: bool = False      # 本次进入是否由我们拉起容器
        self._connect_params = ...                     # 宿主连接参数（现有字段）
        self._container_connect_params: dict | None = None
```

并发互斥不使用 driver 自己的锁，统一使用 `containerService.agent_operation(agent_id)`（§5.13），避免 create / enter / exit / delete 四条路径各自加锁造成顺序不一致。

`AgentDriver` 基类新增成员，供活动记录与 controller 统一读取：

```python
@property
def exec_env(self) -> ExecEnv: return ExecEnv.HOST

@property
def current_container_id(self) -> str | None: return None

async def force_fallback_to_host(self, *, reason: str = "") -> None: return None
```

`TspAgentDriver` 覆写这些成员，返回运行态 `_exec_env` / `_current_container.container_id`，并实现回落。非 TSP driver 保持默认 no-op，容器工具对它们不可见，这些方法只作为防御性兜底。

生命周期约束：

- 启动时连接宿主 gtsp；成功后将 `_host_client` 与 `_client` 指向同一个 client；
- 进入容器时 `_container_client` / `_client` 指向容器 client，`_host_client` **保持连接**（进容器期间宿主侧后台进程状态不丢，退出后立即可用）；
- 退出容器时关闭容器 client，`_client = _host_client`；
- 宿主 client 意外死亡时，按现有 `_ensure_connected` 逻辑重连并重建 `_host_client`；
- `shutdown()` 断开 `_client`、`_container_client`、`_host_client` 全部 client（去重），但**不 stop 容器**；容器由 `containerService.shutdown` / `stop_team_containers` 统一停止。

### 6.2 双 client 的注册与工具列表

`_register_host_tools()` 仍然负责：

1. `host.tool_registry.clear()`；
2. 注册 `_local_tools`（local handler = `funcToolService.run_tool_call`）；
3. 注册当前活跃 client 的 TSP 工具（handler = `_execute_tsp_tool`）；
4. `_apply_tool_policy()` 应用 allow specs。

切换 client 后需要：

```python
self._load_tsp_tools(active_client.tools)
self._register_host_tools()
```

宿主与容器使用同一份 `assets/execute/gtsp` 对应版本，TSP 工具列表一致（外部容器注入的也是同一个二进制）。若用户通过 `command` 配置了自定义宿主 gtsp、版本不一致，容器内仍使用内置 Linux 版，可能出现工具列表差异（见 §13）。

### 6.3 `enter_container` 算法

入口：工具 `enter_container(container)` → `Agent.enter_container(container)` → `TspAgentDriver.enter_container(container)`。

```text
1. 解析目标：containerService.resolve_target(agent_id, container)（§5.11）
   ├─ 解析失败 → container_not_found
   └─ 成功 → target（managed / 外部，带 docker_id / arch / image）
2. async with containerService.agent_operation(agent_id):
3. 若当前已在容器：
   a. 若目标与当前是同一个容器且 client 存活 → 直接返回成功（幂等）
   b. 否则先执行 exit_to_host 的完整流程（含状态恢复），再继续
4. inspect 目标容器 → 记录 entering_state（running / stopped / created）
   ├─ missing → container_lost（managed 则删记录）
   └─ Docker 不可用 → docker_daemon_unavailable
5. 自己创建的容器（managed=True）：
   a. 若 entering_state == running → stop_container（清掉残留主进程）
   b. command = build_managed_command(target)    # docker start -a -i <id>
   c. started_by_us = True（容器会随我们的退出而退出）
   外部容器（managed=False）：
   a. 若 entering_state != running → start_container(target) → 等待 running
      started_by_us = True
   b. 否则 started_by_us = False
   c. command = await prepare_external_gtsp(target)   # docker cp + exec -i
6. client = TSPClient.from_stdio(command, timeout_sec)
   await client.connect()
   try:
       await asyncio.wait_for(client.initialize(...), enter_timeout_sec)
   except TimeoutError / 握手失败:
       client.disconnect()
       _restore_entering_state(target, entering_state)   # 按第 5 步逆操作
       → container_enter_failed（附镜像 / 架构提示）
7. 切换运行态（一次性赋值，不允许中间态）：
   _container_client = client
   _client = client
   _exec_env = CONTAINER
   _current_container = target
   _entered_started_by_us = started_by_us
8. _load_tsp_tools(client.tools); _register_host_tools()
9. publish AGENT_CONTAINER_STATE_CHANGED（container）
10. 返回进入结果（含「是否挂载」提示，§8.3）
```

`_restore_entering_state(target, entering_state)` 是产品 §3.1「进入与退出不影响容器运行状态」的实现：

| target | entering_state | 恢复动作 |
|---|---|---|
| managed | running | 保持 stopped（残留主进程已被清理，不复原；管理容器的正常状态就是 stopped） |
| managed | stopped / created | 保持 stopped（本来就没起来） |
| 外部 | running | 不做任何事（我们没动它的主进程） |
| 外部 | stopped / created | `docker stop`（我们拉起过，失败要停回去） |

关键点：

- **没有 `docker start` 之后再 `docker exec` 的混用**：自己创建的容器一律 `start -a -i`（gtsp 就是主进程），外部容器一律 `exec -i`（不动主进程）。两条路径不交叉。
- **失败一律回落到宿主**，不把 Agent 留在「既不在宿主也连不上容器」的悬空状态；
- `CancelledError` 也要走清理：断开新 client、恢复容器状态、回落宿主后重新抛出；
- 所有路径共用 `containerService.agent_operation(agent_id)` 一把锁。

### 6.4 `exit_to_host` 算法

```text
1. async with containerService.agent_operation(agent_id):
2. 若当前已是 host → 确保 host client 可用（必要时重连），返回成功（幂等）
3. target = _current_container；started_by_us = _entered_started_by_us
4. 关闭容器 client：
   a. 先尝试 client.shutdown()（发 TSP shutdown 请求，让 gtsp 优雅退出）
   b. 超时或失败 → client.disconnect()（直接关 stdin / 杀子进程）
5. 等待容器状态收敛（最多 exit_timeout_sec，每 200ms 轮询一次）：
   ├─ managed=True → 期望 stopped
   ├─ 外部 + started_by_us=True → 期望 stopped
   └─ 外部 + started_by_us=False → 期望仍 running（只等 gtsp 进程退出，不等容器状态）
6. 状态不符合期望：
   a. docker stop -t <stop_timeout_sec>
   b. 仍不符合 → docker kill + warning（含 container_name）
7. 切换运行态（无论第 6 步结果如何）：
   _container_client = None
   _current_container = None
   _entered_started_by_us = False
   _exec_env = HOST
   _client = _host_client
8. 确保 host client 可用；可用时 _load_tsp_tools(_host_client.tools) + _register_host_tools()，
   恢复宿主侧工具列表；失败则结果带 warning（执行环境仍是 host，下次工具调用会再次尝试重连）
9. publish AGENT_CONTAINER_STATE_CHANGED（host）
10. 返回退出结果（含被停止的容器 id，若本次停过）
```

第 5-6 步是 §5.12 的三级兜底；第 7 步的「无论结果如何都回宿主」是本设计的核心取舍。

### 6.5 `_execute_tsp_tool` 的调整

保持现有逻辑：

- 活跃 client 断开 → `_ensure_connected()` 重连；
- 连接断开类 TSPException → 清空 client，下次重连。

新增分支：

1. `_ensure_connected()` 改为按执行环境分派：

   | 环境 | 行为 |
   |---|---|
   | `HOST` | 现有宿主重连逻辑（`_connect_params`）；成功后 `_host_client` 与 `_client` 指向同一个新 client |
   | `CONTAINER` | `_ensure_container_connected()`：用 `_container_connect_params` 重新构造命令（按 target 类型走 A / B），inspect 后必要时 start，再重建 client；成功后同时更新 `_container_client` 与 `_client` |

2. 容器重连时的分类：

   | 情况 | 处理 |
   |---|---|
   | 容器 stopped / created | 按 §6.3 第 5 步重新拉起，再重建 client |
   | 容器 running 但 gtsp 进程已退出 | 直接重建 client（managed：先 stop 再 `start -a -i`；外部：重新 `exec -i`） |
   | 容器 missing | `_handle_container_lost(target)`，返回 `container_lost` |
   | Docker 不可用 | `force_fallback_to_host()`（保留记录），返回 `docker_daemon_unavailable` |
   | gtsp 握手失败 | 断开 client、恢复容器状态、回落宿主，返回 `container_enter_failed` |

3. 调用过程中抛连接断开异常后，再 inspect 一次：
   - missing → 走丢失处理（managed 删记录 + 回落）；
   - Docker 不可用 → 回落宿主、保留记录。

4. 共享目录只读提示：
   - 活跃环境是容器，工具结果失败，且参数或结果中出现 `/workspace/team_shared`，且 stderr / message 含 `read-only file system`（大小写不敏感）；
   - 在 message 后追加：

```text
共享目录默认只读，无法写入。你的个人目录 /workspace/self 可写；
如需写共享目录，请先调用 exit_to_host 回到宿主再写。
```

5. 外部容器的「无挂载」提示（只在进入时给一次，不每次工具调用都提示）：
   - 外部容器里 `/workspace/self` 不存在，用户若访问会得到「not found」；
   - 不做事后补救提示（避免噪声），进入返回体的 `message` 已明确说明。

### 6.6 失败与回落

统一 `force_fallback_to_host(*, reason: str = "")`：

```text
1. 关闭 _container_client（disconnect；不再走 shutdown，避免在异常路径上多等一轮）
2. _container_client = None; _current_container = None; _entered_started_by_us = False
3. _exec_env = HOST; _client = _host_client
4. publish AGENT_CONTAINER_STATE_CHANGED（host）（去重：环境没变则不发布）
5. 若 host client 可用，_load_tsp_tools(_host_client.tools) + _register_host_tools()；
   不可用则尝试重连，失败只记 warning
```

配套方法：

- `_handle_container_lost(target)`：目标已在 Docker 中消失时使用。先 `force_fallback_to_host()`（幂等；当前环境可能已是宿主），managed 则删除该条记录（No such container 视为成功），返回可读消息。目标可能是当前容器，也可能是 `enter_container` 的目标容器。
- `_restore_entering_state(target, entering_state)`：`enter_container` 失败时把容器恢复到进入前状态。

调用场景：

| 场景 | 记录处理 | 返回给 Agent |
|------|----------|--------------|
| 进入时 start 失败 | 保留记录 | `container_start_failed` + 原因 |
| 容器内 gtsp 握手失败 | 恢复进入前状态，保留记录 | `container_enter_failed` + 镜像 / 架构提示 |
| 运行中容器被外部删除 | managed：**删除记录**（释放名额） | `container_lost` + 「文件仍在宿主，请重新创建」 |
| 运行中 Docker daemon 不可用 | 保留记录 | `docker_daemon_unavailable` + 「容器保留，恢复后可重新进入」 |
| 后端重启 | 不恢复，一律回宿主 | 启动日志 info |

注意：**回落宿主不会删除个人目录 / 共享目录文件**。`container_lost` 只删除 managed 的 DB 记录（容器层已经不存在）；其他失败保留记录，Agent 可以再次 `enter_container`。

### 6.7 与 hot reload / 重启的关系

| 动作 | 行为 |
|------|------|
| 单体 Agent 热更新 | 旧 driver `shutdown`：断开容器 / 宿主 client，**不 stop 容器**；新 driver `startup`：只连宿主（不恢复容器），Agent 需重新 `enter_container` |
| Team 热更新 | `stop_team_runtime` 会 stop 当前容器；restore 时 Agent 起在宿主 |
| Team 停用 | 同上；容器与记录保留 |
| Team 删除 | `stop_team_runtime` 停止容器后，`delete_team_containers` 删除记录与容器（§7.5） |
| 后端正常退出 | `containerService.shutdown` 停止本实例 running 容器；记录保留 |
| 后端异常退出 | 容器可能仍在 running（含自己创建的容器 gtsp 主进程）；下次启动 sweep + 收紧 |

**「不恢复」是刻意的**（D9）：产品没有要求重启后自动回容器，反而在 §8 要求「后端崩溃 / 重启后不残留运行中的容器」。不恢复就不需要处理「记录说在容器、实际不在」的问题，也避免后端重启时把一批容器同时拉起来（自己创建的容器拉起即启动一个新的 gtsp 主进程，成本不低）。

热更新期间新 driver 会先连上宿主，旧 driver 稍后才断开容器 client；旧 driver 断开的只是它持有的那条 stdio 管道，若容器里的 gtsp 因此退出，容器也会退出——这是正确的收敛结果，Agent 下次需要时重新进入即可。

---

## 7. 生命周期

### 7.1 后端启动顺序

`backend_main.py` 阶段 1 调整为：

```python
await messageBus.startup()
await llmService.startup()
await funcToolService.startup()
await ormService.startup(app_config.setting.db_path)
await containerService.startup()          # 新增：配置解析 + 按 owner label 清理遗留 running
await persistenceService.startup()
await agentService.startup()
...
```

`containerService.startup()` 必须在 `ormService` 之后（可能读 DB）且在 `agentService` 之前（Agent 启动时容器工具已可见）。

### 7.2 Agent 启动

`TspAgentDriver.startup()`：

```text
1. 建立宿主 client（现有逻辑，一字不改）
2. 不恢复任何容器状态：_exec_env 恒为 HOST，_current_container 恒为 None
3. 若 DB 里该 Agent 有容器记录 → 只打 info 日志（「有 N 个容器记录，当前在宿主」）
4. 建立容器工具可见性：containerService.is_enabled() 决定是否把 5 个工具加入 _local_tools
```

### 7.3 停机顺序

`backend_main.py` finally 中：

```python
schedulerService.shutdown()
await agentService.shutdown()        # driver 断开宿主/容器 client
await containerService.shutdown()    # 新增：stop 本实例 running 容器
await persistenceService.shutdown()
await ormService.shutdown()
...
```

- 必须在 `agentService.shutdown` 之后：先断开 client，再 stop 容器，避免容器 stop 时 client 还在等待工具结果；
- 必须在 `ormService.shutdown` 之前：`containerService.shutdown` 可能读取 DB / 记录；
- `containerService.shutdown` 自身不抛异常，Docker 不可用时跳过。

### 7.4 Team 生命周期

```python
# teamService.stop_team_runtime
schedulerService.stop_scheduler_team(team_id)
await agentService.unload_team(team_id)
await containerService.stop_team_containers(team_id)   # 新增
await roomService.close_team_rooms(team_id)
```

- 停用 / 热更新 / 清空数据 / 删除 Team 都走这里，保证不留下 running 容器；
- `stop_team_containers` 只 stop，不删除记录；重新启用后 Agent 在宿主，可再次 `enter_container`；
- `restore_team` 不变：Agent 加载后执行环境是宿主。

### 7.5 工作目录改名 / 离职 / 删除时的容器清理

容器的 bind mount 在 `docker create` 时固化 host 路径，Docker 没有「修改挂载」的能力。因此只要个人目录或 Team 目录发生移动，已有容器就会指向旧路径：轻则挂载到新建的空目录，重则启动失败。必须在目录移动前清理相关容器。

| 操作 | 入口 | 前置状态 | 处理 |
|------|------|----------|------|
| Agent 改名 | `agentService.overwrite_team_agents` → DB 事务提交后、`remove_agent_workdirs` 之前 | `overwrite_team_agents` 已要求 Team 停用，理论上无运行态 Agent | 对 `agent_renames` 中的 Agent 执行 `delete_agent_containers(ids)`；放在提交后是为了写库失败回滚目录时容器仍然有效。容器层丢失，个人目录文件随改名保留 |
| Agent 改名（工具） | `agentService.rename_agent_workdir` → 目录移动前 | 通过 `save_agent` 工具触发，Agent 可能正在运行 | 若 Agent 已加载：在 `containerService.agent_operation(agent_id)` 锁内 `force_fallback_to_host()`，再 `delete_agent_containers([agent_id])`；未加载则直接删记录 / 容器。清理失败只记 warning，仍继续改名 |
| Agent 离职 | `overwrite_team_agents` → DB 事务提交后、`remove_agent_workdirs` 之前 | 同上 | 对 `ids_to_offboard` 执行 `delete_agent_containers(ids)` |
| Team 改名 | `teamService.rename_team` → `rename_team_workdir` 之前 | 仅当 `team.config` 没有自定义 `working_directory`（目录会移动） | 若 `team.enabled`，先 `stop_team_runtime(team_id)`；再 `delete_team_containers(team_id)`；最后移动目录。自定义目录不移动，容器挂载仍然有效，不做处理 |
| Team 工作目录变更 | `teamService.update_team_base_info` → 保存新配置之前 | 合并 `config_updates` / `working_directory` 后，新旧 `resolve_team_workdir` 结果不同 | 同「Team 改名」 |
| Team 删除 | `teamService.delete_team` → `stop_team_runtime` 之后、删除 DB 之前 | Agent 已卸载 | 执行 `delete_team_containers(team_id)` |
| Team 停用 / 热更新 | `stop_team_runtime` | — | 只 stop 该 Team 的 running 容器，保留记录与容器层 |

Team 改名 / 工作目录变更先停运行时再清理，避免「清理后仍有 turn 进入容器 / 创建新容器」与目录变化并发；停用 Team 没有运行态，直接清理。名称 / 重名 / 配置等前置校验必须在停运行时之前完成，避免无效请求打断运行中的 Team。若随后写库失败、目录回滚，容器环境层已经丢失，Agent 需要重新 `create_container`；若操作前 Team 处于启用状态，失败回滚后与成功保存后都调用 `restore_team(team_id)`（恢复失败只记 error，不覆盖原始异常）。容器层本来就是可丢弃的，个人目录文件随回滚保持完整。

`overwrite_team_agent_employ_status`（部门树同步导致的离职）不移动 / 删除个人目录，因此也不清理容器：与现有「离职但保留目录」策略一致，重新在岗后仍可恢复原来的容器环境。Team 删除时统一清理。

清理失败的处理：

- `delete_agent_containers` / `delete_team_containers` 先 `docker rm -f`，再删 DB 行；
- Docker 不可用或 `rm` 失败时，**仍然删除 DB 行**（目录已移动 / Agent 已删除，记录再也不能安全启动），并记录 warning，带上 `container_name` 便于人工 `docker rm -f`；
- 除 `save_agent` 工具路径外，调用方保证清理前 Agent 已卸载；`save_agent` 路径由 `rename_agent_workdir` 先做回落。因此 `delete_*_containers` 本身不回调 driver，也不发布 WS 事件；
- 整个清理是 best-effort：内部捕获所有异常，不向调用方抛出；目录改名 / 删除不能因为 Docker 异常而失败。

---

## 8. 工具契约

### 8.1 注册、可见性与分类

`funcToolService/core.py` 的 `_func_tools`：

```python
_func_tools = {
    ...,
    "create_container": FuncTool("create_container", create_container),
    "delete_container": FuncTool("delete_container", delete_container),
    "list_containers": FuncTool("list_containers", list_containers),
    "enter_container": FuncTool("enter_container", enter_container),
    "exit_to_host": FuncTool("exit_to_host", exit_to_host),
}
```

`funcToolService/tools.py` 重新导出五个函数，保证工具 schema 快照脚本的 `getattr(tools, name)` 能发现它们：

```python
from .containerTools import (
    create_container,
    delete_container,
    enter_container,
    exit_to_host,
    list_containers,
)
```

可见性：

```python
def get_tools(*, include_container_tools: bool = False) -> list[OpenAITool]:
    ...
```

- `TspAgentDriver`：`get_tools(include_container_tools=containerService.is_enabled())`；
- `NativeDriver` / `ClaudeSdkDriver`：`get_tools()`（默认 false）；
- 容器工具仍注册在全局 `_func_tools` 中，`run_tool_call` 可按名字找到；但未注册进当前 driver 的 `tool_registry` 时，turn runner 会返回「工具未找到」，不会执行。

分类：五个工具都归 `ToolCategory.EXECUTE`，与 `execute_bash` 同权限域。Agent 没有 Execute 权限时看不到容器工具——没有执行能力就不需要切换执行环境。

### 8.2 `create_container`

```python
async def create_container(_context: ToolCallContext = None) -> dict:
    """创建一个带自动挂载的 Docker 容器。

    只创建容器，不切换执行环境。容器内个人目录为 /workspace/self（双向），
    团队共享目录只读挂载为 /workspace/team_shared。
    使用系统内置镜像，不接受 image 参数。
    同一 Agent 最多 3 个容器；达到上限需先 delete_container。
    创建后用 enter_container 进入。
    """
```

返回：

```json
{
  "success": true,
  "container_id": "abc123def456",
  "container_name": "agent-小马哥-1",
  "state": "created",
  "mounts": {
    "self": "/workspace/self",
    "team_shared": "/workspace/team_shared"
  },
  "message": "容器已创建（container_name=agent-小马哥-1）。当前执行环境未改变。使用 enter_container(\"abc123def456\") 进入；个人目录已映射到 /workspace/self。"
}
```

实现要点：

- 从 `_context.agent_id` 取 `agentService.get_agent_or_none`；不存在 → `agent_not_found`；
- 校验 `agent.supports_container_env`（TSP driver 且容器功能开启）；
- `async with containerService.agent_operation(agent_id):` 内调用 `containerService.create_container(...)`；
- **不返回 `exec_env` / `current_container_id`**：创建不改变执行环境，返回这两个字段反而让模型以为变了；若确实需要，模型可调 `list_containers`。

### 8.3 `enter_container`

```python
async def enter_container(
    container: str,
    _context: ToolCallContext = None,
) -> dict:
    """进入一个已存在的容器，在当前容器内执行文件与命令工具。

    Args:
        container: 目标容器，可以是 create_container 返回的 container_id，
            也可以是容器名（如 agent-小马哥-1 或你自己 docker run 起的名字）。
            不接受镜像名。
    """
```

校验：

- `container` 为空 → `invalid_arguments`；
- 解析后容器不存在 → `container_not_found`；兜底消息区分两种情况：
  - `container` 看起来像镜像名（含 `:` 或 `/`）→ 追加「如果你想用某个镜像，请先在宿主机上执行 `docker run` 起一个容器，再进入该容器」；
  - 否则 → 追加「可用 `docker ps -a` 查看宿主机上的容器」；
- 容器 OS 不是 linux → `container_image_not_linux`；
- 容器架构不是 amd64 / arm64 → `container_arch_unsupported`。

返回（自己创建的容器）：

```json
{
  "success": true,
  "exec_env": "container",
  "container_id": "abc123def456",
  "container_name": "agent-小马哥-1",
  "mounted": true,
  "workdir": "/workspace/self",
  "message": "当前执行环境=容器 agent-小马哥-1。文件读写、命令执行、进程管理工具现在都在容器内执行；个人目录对应 /workspace/self（双向），共享目录 /workspace/team_shared 只读。宿主路径不再可用。"
}
```

返回（外部容器）：

```json
{
  "success": true,
  "exec_env": "container",
  "container_id": "docker:9f2c8a1b3d4e",
  "container_name": "mysql-test",
  "mounted": false,
  "workdir": "/tmp/togospace/work",
  "message": "当前执行环境=容器 mysql-test。该容器不是本系统创建的，没有挂载你的个人目录：容器内写入的文件不会回到宿主，需要时请手动搬运；你的个人目录路径在容器内不可用。容器原本处于运行状态，退出后仍会继续运行。"
}
```

`mounted=false` 的提示文案按「进入前状态」区分：

| 进入前状态 | 追加说明 |
|---|---|
| running | 「容器原本处于运行状态，退出后仍会继续运行」 |
| stopped / created | 「容器原本处于停止状态，退出后会停回停止状态」 |

### 8.4 `exit_to_host`

```python
async def exit_to_host(_context: ToolCallContext = None) -> dict:
    """退出当前容器，回到宿主机执行环境。

    无参数。若当前已在宿主机，调用是幂等的。
    """
```

无参数，schema 为 `{"type":"object","properties":{},"required":[]}`。

返回：

```json
{
  "success": true,
  "exec_env": "host",
  "stopped_container_id": "abc123def456",
  "workdir": "/path/to/team/agent/<name>",
  "message": "当前执行环境=宿主。容器 agent-小马哥-1 已停回停止状态（容器与已安装内容保留，再次 enter_container 会重新启动）。容器内的 /workspace/self 路径不再可用，请使用宿主个人目录路径。"
}
```

`stopped_container_id`：本次退出实际停止的容器；若进入前容器就在 running（外部容器）或退出时没能停掉，则为 `null`。

### 8.5 `list_containers`

```python
async def list_containers(_context: ToolCallContext = None) -> dict:
    """列出你自己创建的容器及其状态。

    只包含 create_container 创建的容器，不包含宿主机上其他容器
    （但你仍然可以用 enter_container 指名进入它们）。
    """
```

返回：

```json
{
  "success": true,
  "docker_available": true,
  "exec_env": "host",
  "current_container_id": null,
  "containers": [
    {
      "container_id": "abc123def456",
      "container_name": "agent-小马哥-1",
      "state": "stopped",
      "is_entered": false,
      "image": "ubuntu:24.04",
      "created_at": "2026-10-31T10:00:00"
    }
  ]
}
```

行为：

- **只返回自己创建的**（产品 §4.4）：不 `docker ps` 枚举全集，只按 DB 记录 inspect；
- `is_entered` 由 driver 运行态填充，`containerService` 不知道谁是当前容器；
- Docker 不可用但有记录 → `docker_available=false`，各容器 `state=unknown`，顶层 `message` 说明；
- 无容器 → `containers=[]`（产品 §5「借用已有的机器」场景里，Agent 看到空列表但知道名字仍可进入）。

### 8.6 `delete_container`

```python
async def delete_container(
    container_id: str,
    _context: ToolCallContext = None,
) -> dict:
    """删除你自己创建的容器。

    Args:
        container_id: 要删除的容器 ID（create_container 返回的 ID）。
    """
```

流程：

1. 校验 id 格式（12 位 hex，大小写归一化）；非法 → `invalid_arguments`；
2. `async with containerService.agent_operation(agent_id):` 内查记录（带 agent_id）；不存在 → `container_not_found`（含「只能删除自己用 create_container 创建的容器」提示）；
3. **若该容器是当前正在使用的容器 → `container_in_use`**，不删：

```json
{
  "success": false,
  "error_code": "container_in_use",
  "message": "容器 abc123def456 正在使用中，无法删除。请先调用 exit_to_host 退出该容器，再删除。"
}
```

4. `containerService.delete_container(agent_id, container_id)`：
   - `docker rm -f` 成功、或返回 No such container → 删 DB 行；
   - Docker 不可用 / 其他失败 → 保留 DB 行，返回 `container_delete_failed`；
5. 返回：

```json
{
  "success": true,
  "deleted_container_id": "abc123def456",
  "message": "容器 agent-小马哥-1 已删除。个人目录与共享目录文件不受影响；该容器内安装的环境层内容已消失。"
}
```

**注意与旧方案的区别**：旧设计是「删除当前容器 → 自动踢回宿主」；本版按产品 §2.4 改为**直接拒绝**，要求先 `exit_to_host`。好处是删除变成纯粹的管理动作，不隐含环境切换副作用。

### 8.7 docstring / schema 约定

- 私有参数 `_context` 不写进 `Args:` 段（现有解析器规则）；
- `create_container` / `exit_to_host` / `list_containers` 没有公开参数，schema 为 `{"type":"object","properties":{},"required":[]}`；
- `enter_container` 参数名用 `container`（不是 `container_id`），因为既接受 ID 也接受容器名；
- 参数说明保持「模型能直接执行」的口吻，不写内部实现细节；不出现 `docker_id`、`attach`、`exec` 等实现词；
- 工具 schema 快照更新必须走：

```bash
.venv/bin/python3 scripts/tool_schema_snapshot.py --update
```

- 同步更新 `tests/unit/service/funcToolService/test_tool_schema_snapshot.py` 的 `_DECLARED_PARAMETER_ORDER`：

```python
"create_container": [],
"delete_container": ["container_id"],
"enter_container": ["container"],
"exit_to_host": [],
"list_containers": [],
```

- 同步更新 `tests/unit/service/agentService/test_tool_registry.py` 的 `EXPECTED_TOOL_CATEGORIES`（37 → 42 条，EXECUTE 4 → 9 条）。

### 8.8 错误码汇总

见 §5.3；工具层额外：

| error_code | 场景 |
|------------|------|
| `invalid_arguments` | 参数格式非法、`container` 为空 |
| `agent_not_found` | 工具上下文中的 Agent 不在运行态 |
| `container_in_use` | 删除正在使用的容器（§8.6） |
| `container_not_found` | 容器不存在 / 不是自己创建的（delete 时） |

所有错误返回体统一为：

```json
{"success": false, "message": "……", "error_code": "……"}
```

---

## 9. 后端接口与 Web

### 9.1 Agent payload

`agentController.py`：

- `AgentListHandler`（`GET /agents/list.json`）每个 Agent 增加：

```json
{
  "exec_env": "host",
  "current_container_id": null,
  "container_count": 1
}
```

- `_build_agent_detail_payload`（`GET /agents/<id>.json`）增加同上字段，并附带容器列表：

```json
{
  "exec_env": "container",
  "current_container_id": "abc123def456",
  "containers": [
    {
      "container_id": "abc123def456",
      "container_name": "agent-小马哥-1",
      "state": "running",
      "is_entered": true,
      "image": "ubuntu:24.04",
      "created_at": "2026-10-31T10:00:00"
    }
  ]
}
```

实现：

- 列表：`exec_env` / `current_container_id` 取已加载 Agent 的 driver 运行态（`agentService.get_team_agents(team_id)`）；未加载的 Agent 恒为 `host` / `null`（执行环境不持久化，D9）。`container_count` 用 `containerService.count_map_by_agent_ids(agent_ids)` 一次查 DB 统计，不碰 Docker；
- 详情：已加载 Agent 以运行态为准；容器列表由 `containerService.describe_containers(agent_id)` 实时 inspect（按 §5.8 并行、整体 3s 超时），不因 `missing` 主动删除记录；
- 非 TSP Agent 不会产生容器记录，自然返回 host；controller 不需要按 driver 分支；
- 容器对象用 `ContainerInfo.model_dump(mode="json")`，不手工拼字段；
- 不为前端方便添加兼容字段。

### 9.2 WebSocket

新增消息总线 topic：

```python
# constants.py
AGENT_CONTAINER_STATE_CHANGED = auto()
# payload: gt_agent(GtAgent), exec_env(str: "host"|"container"), current_container_id(str|None)
```

`wsController._WS_TOPICS` 增加该 topic，事件映射：

```python
if msg.topic == MessageBusTopic.AGENT_CONTAINER_STATE_CHANGED:
    payload["event"] = "agent_container"
```

事件示例：

```json
{
  "event": "agent_container",
  "gt_agent": {"id": 12, "name": "xiao_ma", "team_id": 3},
  "exec_env": "container",
  "current_container_id": "abc123def456"
}
```

发布实现：driver 内的 `_publish_exec_env_changed()` 调用 `messageBus.publish(...)`；保存上一次发布值用于去重。`containerService` 本身不发布事件，只由持有运行态的 driver 发布。

发布时机：

- `enter_container` 成功后；
- `exit_to_host` 成功后；
- 容器丢失、Docker 不可用导致的回落；
- `save_agent` 工具改名导致当前容器回落宿主时。

只在 `exec_env` 或 `current_container_id` 变化时发布；`create_container`（不切换）和删除非当前容器不发布。

### 9.3 前端改动

| 文件 | 改动 |
|------|------|
| `frontend/src/types.ts` | 新增 `ExecEnv`（`'host'` / `'container'`）；`AgentInfo` 增加可选 `exec_env` / `current_container_id` / `container_count`；`AgentDetail` 增加 `containers`；新增 `WsAgentContainerEvent`，加入 `WsEvent` |
| `frontend/src/api.ts` | `normalizeAgentDetail` / 列表 normalize 映射新字段；`ContainerInfo` 类型 |
| `frontend/src/realtime/eventNormalizer.ts` | 识别 `agent_container`，返回 `{type:'agent_container', teamId, agentId, execEnv, currentContainerId}` |
| `frontend/src/realtime/runtimeStore.ts` | 按 `agent_status` 的模式更新 `teamAgentsState` 对应 Agent 的 `exec_env` / `current_container_id` |
| `frontend/src/components/agent/AgentListSection.vue` | Agent 卡片在 `exec_env === 'container'` 时显示「容器」徽标（含容器名），宿主不额外占位 |
| `frontend/src/components/agent/AgentActivityItem.vue` | `tool_call` 活动在标题旁显示执行环境徽标（来自活动 metadata） |
| `frontend/src/components/agent/AgentPropertiesPanel.vue` | 「执行环境」区块：当前环境（宿主 / 容器名）；有容器时展示表格：容器名、状态、是否当前、镜像、创建时间 |
| `frontend/src/locales/zh-CN.json` / `en.json` | 新增 `agent.execEnv.*`、`agent.containers.*`、`agent.containerState.*` |

活动记录：

- `AgentActivityMeta` 增加 `exec_env: str | None`、`current_container_id: str | None`、`container_name: str | None`；
- `AgentTurnRunner._run_tool_to_item` 创建活动时写入调用开始时的环境，结束时通过 `metadata_patch` 写入调用完成后的环境（`enter_container` / `exit_to_host` / `delete_container` 的最终状态才是用户关心的）；
- 前端活动条目复用 `agent.execEnv.*` 文案显示「宿主」/「容器 agent-小马哥-1」。

状态展示：

| state | 中文 | English |
|-------|------|---------|
| `created` | 已创建 | Created |
| `running` | 运行中 | Running |
| `stopped` | 已停止 | Stopped |
| `missing` | 已丢失 | Missing |
| `unknown` | 未知 | Unknown |

### 9.4 TUI

本次不改 TUI。产品只要求 Web 可见；TUI 后续可在 Agent 信息栏复用同一接口字段。

---

## 10. 安全设计

1. **不挂 `docker.sock`**：容器内无法控制宿主 Docker；
2. **不 privileged、不额外 cap**：使用 Docker 默认权限；`--security-opt no-new-privileges` 默认开启；
3. **mount 白名单**（仅对 `create_container`）：只挂个人目录、共享目录（只读）、gtsp 二进制（只读），不挂宿主其他路径；
4. **归属校验**：`delete_container` / `list_containers` 严格按 `(agent_id, container_id)` 查 DB；LLM 无法通过 create 传入 docker id / 容器名 / 镜像 / mount；
5. **`enter_container` 有意放宽**（产品 §7 已知风险）：进入一个已存在的容器等于继承它的全部可见面——环境变量、它挂载的所有宿主路径。若该容器挂载了宿主 Docker 控制通道或宿主根目录，Agent 进入后能看到对应内容。本版按「先实现功能」取舍，**不做检测与拦截**；后续版本可加「挂载探测 + 拒绝列表」；
6. **参数校验**：`container_id` 严格 12 位 hex；`enter_container` 的 `container` 走 `docker inspect` 解析，不拼 shell；docker 命令全部 argv 执行，禁止 `shell=True`；
7. **容器内 gtsp 路径与准备命令是常量**：`docker cp` 源固定来自我们自己的 assets，目标路径与 `exec` 参数全是常量，不拼接 LLM 输入；
8. **共享目录只读**：bind mount `readonly`，容器内写入返回 EROFS；后端补一条可读提示；
9. **网络两档**：`bridge` / `none`；敏感任务可断网；
10. **不继承后端环境变量**：`docker create` 只传 `HOME`；API key、token 不会进入容器；
11. **日志脱敏**：不打印完整 docker 环境、不打印用户配置中的密钥；stderr 截断；
12. **容器可见范围**：自己创建的容器内只能看到个人目录、只读共享目录和容器自身文件系统，看不到其他 Agent 目录与宿主数据目录；
13. **删除边界**：删除容器不影响个人目录 / 共享目录文件；容器层内容随删除消失；
14. **安全边界说明**：容器是「环境隔离」而非强安全沙箱。默认以镜像用户（root）运行、与宿主共享内核；对不可信代码应额外配置 `network=none`、内存 / CPU 限制，必要时使用 rootless Docker 或虚拟机。宿主模式（不切换）仍然没有容器隔离，Agent 的宿主命令不受保护。

---

## 11. 改动文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `src/constants.py` | 修改 | `ExecEnv`、`ContainerState`、`MAX_CONTAINERS_PER_AGENT`、`DEFAULT_CONTAINER_IMAGE`、label 常量、`MessageBusTopic.AGENT_CONTAINER_STATE_CHANGED` |
| `src/util/configUtil/configTypes.py` | 修改 | `ContainerConfig` + `SettingConfig.container` |
| `src/appPaths.py` | 修改 | `get_gtsp_linux_binary_path(arch)` |
| `src/util/logUtil.py` | 修改 | 新增 `service.containerService` 日志拆分（`service/containerService.log`，`global=True`） |
| `src/model/dbModel/gtAgentContainer.py` | 新增 | `GtAgentContainer`（含 `seq`，无 `is_current`） |
| `src/model/coreModel/containerModel.py` | 新增 | `ContainerTarget`、`ContainerInfo`、`ContainerListResult` |
| `src/dal/db/gtAgentContainerManager.py` | 新增 | 容器记录 DAL |
| `src/dal/db/__init__.py` | 修改 | 导出新 manager |
| `assets/migrate/0017_agent_containers.sql` | 新增 | 建表 + 索引 |
| `src/service/containerService/__init__.py` | 新增 | 对外导出 |
| `src/service/containerService/core.py` | 新增 | 容器生命周期 / 状态 / 目标解析 |
| `src/service/containerService/dockerCli.py` | 新增 | docker 执行与错误分类 |
| `src/service/containerService/gtspInContainer.py` | 新增 | 三种形态的容器内 gtsp 命令构造 |
| `src/service/funcToolService/containerTools.py` | 新增 | 五个工具实现 |
| `src/service/funcToolService/tools.py` | 修改 | 重新导出容器工具 |
| `src/service/funcToolService/core.py` | 修改 | 注册工具、`get_tools` 增加过滤参数 |
| `src/service/agentService/toolRegistry.py` | 修改 | 五个工具归入 `EXECUTE` |
| `src/service/agentService/driver/tspDriver.py` | 修改 | 双 client、进入 / 退出、重连分派、丢失处理、只读提示、环境事件 |
| `src/service/agentService/driver/base.py` | 修改 | `AgentDriverHost.shared_workdir`；`exec_env` / `current_container_id` 默认属性；`force_fallback_to_host` 默认 no-op |
| `src/service/agentService/agentTurnRunner.py` | 修改 | 持有 `shared_workdir`；活动 metadata 写入执行环境 |
| `src/service/agentService/agentTaskConsumer.py` | 修改 | 透传 `shared_workdir` |
| `src/service/agentService/agent.py` | 修改 | 新增 `agent_workdir` / `shared_workdir` / `exec_env` / `current_container_id` / `supports_container_env` 属性；`enter_container` / `exit_to_host` / `force_fallback_to_host` 转发 |
| `src/service/agentService/core.py` | 修改 | `Agent(...)` 传入 `shared_workdir`；`rename_agent_workdir` 移动目录前清理目标 Agent 容器（§7.5） |
| `src/service/agentActivityService.py` | 修改 | `AgentActivityMeta` 增加 `exec_env` / `current_container_id` / `container_name` |
| `src/service/teamService.py` | 修改 | `stop_team_runtime` 停止容器；`rename_team` / `update_team_base_info` / `delete_team` 清理容器（§7.5） |
| `src/backend_main.py` | 修改 | 启动 / 停机接入 `containerService` |
| `src/controller/agentController.py` | 修改 | 列表 / 详情 payload 增加执行环境字段与容器列表 |
| `src/controller/wsController.py` | 修改 | 新 topic + `agent_container` 事件 |
| `scripts/togo_desktop.spec` | 修改 | 移除 `gtsp-linux-*` 排除，桌面包携带 Linux gtsp |
| `assets/config_template.json` | 修改 | 增加 `container` 配置段 |
| `assets/docs/setting.README.md` | 修改 | 中英文配置说明 |
| `assets/execute/gtsp/gtsp-linux-amd64` | 更新 | 静态链接产物（见 §5.6） |
| `assets/execute/gtsp/gtsp-linux-arm64` | 更新 | 静态链接产物确认 |
| `frontend/src/types.ts` | 修改 | 类型 |
| `frontend/src/api.ts` | 修改 | 字段映射 |
| `frontend/src/realtime/eventNormalizer.ts` | 修改 | 新事件 |
| `frontend/src/realtime/runtimeStore.ts` | 修改 | 状态更新 |
| `frontend/src/components/agent/AgentListSection.vue` | 修改 | 容器徽标 |
| `frontend/src/components/agent/AgentActivityItem.vue` | 修改 | 活动执行环境徽标 |
| `frontend/src/components/agent/AgentPropertiesPanel.vue` | 修改 | 执行环境区块 |
| `frontend/src/locales/zh-CN.json` / `en.json` | 修改 | i18n |
| `tests/unit/service/containerService/*` | 新增 | 容器服务单测 |
| `tests/unit/service/agentService/test_tsp_driver_container.py` | 新增 | 进入 / 退出 / 回落单测 |
| `tests/integration/test_container_service/*` | 新增 | fake docker 集成测试 |
| `tests/lifecycle/migrate/test_migration_0017.py` | 新增 | 迁移测试 |
| `tests/unit/service/agentService/test_workdir_rename.py` | 修改 | 改名 / 离职清理容器 |
| `tests/unit/service/teamService/test_team_rename.py` | 修改 | Team 改名 / 工作目录变更时停运行时与清理容器 |
| `tests/unit/service/funcToolService/test_tool_schema_snapshot.py` | 修改 | 参数顺序表 |
| `tests/unit/service/agentService/test_tool_registry.py` | 修改 | 分类契约 |
| `tests/fixtures/tool_schema_snapshot.json` | 修改 | `--update` 生成 |
| `tests/lifecycle/backend/test_backend_main_shutdown.py` | 修改 | 启停顺序断言 |
| `tests/unit/service/agentService/test_tsp_driver.py` | 修改 | `_client` 语义保持兼容，必要时补 `_host_client` 初始化 |

---

## 12. 测试方案

### 12.1 单元测试

`tests/unit/service/containerService/`：

| 用例 | 覆盖 |
|------|------|
| `test_docker_cli_resolve.py` | 显式配置、`which`、macOS / Windows 常见路径、找不到 |
| `test_error_classification.py` | daemon 未运行、镜像缺失、mount denied、No such container、`exec format error`、超时 |
| `test_create_container.py` | 上限 3、name/seq 生成、`--entrypoint` 与 `--mode stdio` 参数、`--init`/`--network`、镜像缺失不自动 pull、Docker 不可用、创建超时补偿删除、写库失败补偿 |
| `test_create_concurrency.py` | 同一 Agent 并发创建不会超过 3、seq 不重复；达到上限时 `prune_missing` 释放僵尸记录 |
| `test_resolve_target.py` | 12 位 hex 命中 DB / 未命中转 docker；容器名精确匹配；ID 前缀匹配；labels 反查归属；镜像名 → `container_not_found`；外部容器 `managed=False` |
| `test_delete_in_use.py` | 删除当前容器返回 `container_in_use`；非当前容器正常删除；Docker 删除失败保留记录 |
| `test_rename_cleanup.py` | `delete_agent_containers` / `delete_team_containers` 的 rm + 删行、失败降级、按 owner/agent 归属清理；Team 编排见 `test_team_rename.py` |
| `test_lifecycle.py` | start 幂等、stop 超时 kill、rm 不存在视为成功、rm 失败保留 DB |
| `test_describe.py` | created/running/stopped/missing/unknown 映射；只返回 DB 记录（不枚举宿主容器） |
| `test_gtsp_in_container.py` | 形态 A 命令含 `start -a -i` 且**不含** `-t`；形态 B 先 `mkdir`/`cp`/`chmod` 再 `exec -i <binary>`（不经 shell）；形态 C 与现有 `build_gtsp_command` 一致 |
| `test_binary_staging.py` | 从 assets 复制、chmod、已存在复用、架构文件名 |
| `test_config.py` | 默认配置、`image` 不可配置、非法 network / 资源格式校验 |

`tests/unit/service/agentService/test_tsp_driver_container.py`（fake client + fake containerService）：

| 用例 | 覆盖 |
|------|------|
| `test_enter_managed_stopped` | `stop`（若 running）→ `start -a -i` → client → `_client` 切换 → 事件 |
| `test_enter_managed_running` | 先 stop 残留主进程再 `start -a -i` |
| `test_enter_external_running` | `exec -i`、不动容器、`started_by_us=False` |
| `test_enter_external_stopped` | `docker start` → running → `exec -i` → `started_by_us=True` |
| `test_enter_same_container_idempotent` | 不重复 start |
| `test_enter_switch_between_containers` | 从 A 直接进 B：先完成 A 的退出流程 |
| `test_enter_failure_restores_state` | 握手失败：容器恢复进入前状态 + 回落宿主 |
| `test_enter_timeout` | `enter_timeout_sec` 生效，`container_enter_failed` |
| `test_exit_managed_stops` | 关 stdin → 轮询到 stopped → `_client = _host_client` |
| `test_exit_external_keeps_running` | `started_by_us=False`：不停容器 |
| `test_exit_external_started_by_us_stops` | `started_by_us=True`：停回 stopped |
| `test_exit_three_level_fallback` | 关 stdin 不停 → `docker stop`；仍不停 → `docker kill` + warning |
| `test_exit_always_returns_host` | 容器没停成功也切回宿主 |
| `test_container_lost_deletes_record` | 外部删除当前容器：managed 删记录、回落宿主 |
| `test_container_lost_external_no_record` | 外部容器丢失：回落宿主，不动 DB |
| `test_docker_unavailable_keeps_record` | Docker 不可用：回落宿主、保留记录 |
| `test_host_client_kept_alive` | 进入容器后 `_host_client` 仍存在 |
| `test_cancelled_enter_cleans_up` | CancelledError 清理 |
| `test_startup_does_not_restore` | 启动一律 host，不自动进容器 |
| `test_shutdown_does_not_stop_container` | 热更新语义 |
| `test_shared_read_only_hint` | EROFS + team_shared 追加提示 |

`tests/unit/service/agentService/test_workdir_rename.py`（补充）：

| 用例 | 覆盖 |
|------|------|
| `test_save_agent_rename_cleans_loaded_agent` | `save_agent` 改名：锁内回落宿主 + `delete_agent_containers`，再移动目录 |
| `test_save_agent_rename_cleans_unloaded_agent` | 目标未加载时直接删记录 / 容器，不加锁、不回落 |
| `test_save_agent_rename_cleanup_failure_continues` | 清理失败只记 warning，目录仍然完成改名 |
| `test_overwrite_team_agents_rename_cleanup` | 全量覆盖改名 / 离职在提交后清理容器；写库失败回滚目录时不误删 |

### 12.2 集成测试（fake docker）

`tests/integration/test_container_service/`：

- 在临时目录放一个可执行的 fake `docker` 脚本（Python），通过 `FAKE_DOCKER_STATE` 维护 JSON 状态，支持：
  `image inspect`、`create`、`start`、`start -a -i`、`stop`、`kill`、`inspect`、`rm`、`ps`、`exec -i`；
- `start -a -i` / `exec -i` 要能**真的挂一个子进程的 stdio**，以便验证 stdin 关闭后的行为（可用宿主 gtsp 假装容器内 gtsp）；
- 配置 `container.docker_binary` 指向该脚本；
- 用真实 SQLite + 服务启动，走 `funcToolService.run_tool_call` 调用五个工具；
- 校验：DB 行、`seq`、工具返回、`messageBus` 事件、`_exec_env` 变更。

**fake docker 的 `exec -i` 增强（重要）**：让它启动**宿主 gtsp**（darwin / arm64）并把 `/workspace/self` 翻译为真实 agent workdir，从而在不装 Docker 的 CI 里跑通完整的「进入 → 工具调用 → 退出」链路。

### 12.3 生命周期测试

- `tests/lifecycle/migrate/test_migration_0017.py`：
  - 迁移前数据库无 `agent_containers`；
  - 执行到 0017 后表、索引存在；
  - 无 `is_current` 列；
  - 重复执行幂等。
- `tests/lifecycle/backend/test_backend_main_shutdown.py`：
  - 启动顺序新增 `containerService`（在 `ormService` 之后、`agentService` 之前）；
  - 停机顺序新增 `containerService`（在 `agentService` 之后、`persistenceService` 之前）；
  - 断言列表同步更新；
  - monkeypatch 掉真实 Docker 调用（`startup` 的 owner label sweep 也要 mock）。

### 12.4 工具 schema 快照

- `tests/fixtures/tool_schema_snapshot.json` 通过脚本更新；
- `_DECLARED_PARAMETER_ORDER` 增加五条（见 §8.7）；
- `test_tool_registry.py` 的 `EXPECTED_TOOL_CATEGORIES` 增加五条，注释计数改为 42（EXECUTE 9）；
- 确认 `get_tools()` 默认不包含容器工具、`get_tools(include_container_tools=True)` 包含。

### 12.5 真实 Docker e2e（可选，环境变量门控）

`TOGOSPACE_TEST_DOCKER=1` 时运行，前置：本机 Docker 可用、`ubuntu:24.04` 已 pull。

**第一优先级（未验证行为，见 §5.12）**：

0a. 形态 A：`docker run -i --entrypoint /opt/togospace/gtsp ubuntu:24.04 --mode stdio` → 关 stdin → 容器是否 stopped；
0b. 形态 B：对 running 容器 `docker exec -i` → 关 stdin → 容器是否仍 running；
0c. `docker stop` 能否终止 pid1 = gtsp 的容器。

**功能验收**：

1. 创建 → 记录 created，`list_containers` 显示 created；
2. `enter_container` → `execute_bash "pwd"` 输出 `/workspace/self`；
3. 容器内写文件 → 宿主个人目录可见、内容一致；
4. 容器内写 `/workspace/team_shared` → 失败并带只读提示；
5. `exit_to_host` → 容器 stopped，宿主工具正常；
6. 再次 `enter_container` 同一容器 → running，容器层 `apt install` 的内容仍在；
7. 创建第 4 个 → `container_limit_reached` 并提示删除；
8. 删除当前容器 → `container_in_use`，提示先 `exit_to_host`；
9. 外部容器：`docker run -d --name t1 ubuntu:24.04 sleep infinity` → `enter_container("t1")` 成功、提示无挂载 → `exit_to_host` → 容器仍 running；
10. 外部容器（停止态）：`docker stop t1` → `enter_container("t1")` → 自动拉起 → `exit_to_host` → 停回 stopped；
11. `enter_container("ubuntu:24.04")` → `container_not_found` 且提示先 `docker run`；
12. `list_containers` 不出现 `t1`（外部容器不可见），但 `enter_container("t1")` 仍可进入；
13. 宿主 ↔ 容器切换 ≥3 次，无残留进程（`docker ps` 检查）；
14. 后端重启 → 当前环境是宿主，容器记录仍在 `list_containers` 中显示 stopped。

### 12.6 验收映射

| 产品验收项（step1 §8） | 技术落点 |
|------------|----------|
| `create_container` 只创建、自动挂载、返回 id 与名字 | §5.4 / §8.2；e2e 1 |
| 容器名 `agent-<agent名>-<序号>` | §5.5 |
| 不接受 `image` 参数，默认 `ubuntu:24.04` | §4.2 硬编码 + §8.2 无参数；e2e 容器内 `cat /etc/os-release` |
| 个人目录双向映射 | §5.4 mount `/workspace/self`；e2e 3 |
| 共享目录只读 | §5.4 + §6.5；e2e 4 |
| 再次进入容器层依赖仍在 | §5.10 形态 A（stop/start 不删容器）；e2e 6 |
| 运行中容器再次进入可接管 | §6.3 第 5a 步（先 stop 再 start）|
| 后端重启不残留 running | §5.9 sweep + §7.2 不恢复 |
| `enter_container` 传镜像名被拒绝 | §5.11 + §8.3；e2e 11 |
| 自己 `docker run` 的容器可进入 + 无挂载提示 | §5.10 形态 B + §8.3 `mounted=false`；e2e 9 |
| 进入 running 外部容器，退出后仍 running | §6.4 第 5-6 步；e2e 9 |
| 进入 stopped 容器，退出后停回 stopped | §6.3 `_restore_entering_state` / §6.4；e2e 10 |
| 最多 3 个（只算自己创建的） | §5.13 + §8.2；e2e 7 |
| 同一时刻只进入一个 | §6.3 第 3 步先退出 |
| 删除正在使用的容器报错 | §8.6；e2e 8 |
| `list_containers` 只返回自己创建的 | §5.8；e2e 12 |
| 外部删除可读提示 | §5.14 / §6.6 |
| 五类异常提示 | §5.3 |
| 重启恢复（回宿主） | §7.2 |
| Docker 不可用时原有能力不受影响 | §5.3 + §8.1 仅 TSP/EXECUTE 可见 + 默认 host |
| Web / 活动记录显示执行环境 | §9.1 – §9.3 |
| 未使用容器的 Agent 零变化 | §8.1 可见性 + 默认 host |

---

## 13. 风险与待定项

| 风险 | 影响 | 对策 |
|------|------|------|
| **关闭 stdin 不保证容器停止**（§5.12 未实测） | 退出慢（多等 15 秒）或残留 running 容器 | 三级兜底：关 stdin → `docker stop` → `docker kill` + warning；启动时 owner label sweep 收敛 |
| **形态 B 关 stdin 可能把别人的容器停掉** | 违反产品 §3.1「进入运行中容器，退出后继续运行」 | 实现前先跑 §12.5 的 0a/0b/0c；若关 stdin 会让外部容器退出，改为「不等待状态 + 只要 `started_by_us=False` 就绝不 stop」，并在进入时提示「gtsp 退出可能影响容器主进程」 |
| `gtsp-linux-amd64` 静态化不彻底 | `ubuntu:20.04` / alpine 仍跑不起来 | **已处理**：TSP `8c5e131` 用 `CGO_ENABLED=0` 静态化，4 处构建配置均加静态链接断言防回归；任务 #105 验收覆盖 alpine / 20.04 / bookworm-slim / 24.04 四镜像 |
| `CGO_ENABLED=0` 后纯 Go resolver 的 DNS 行为差异 | 容器内解析 `/etc/hosts`、`localhost`、短域名可能与 cgo 版不同 | 静态化任务里要求写明差异；e2e 验证容器内 `getent hosts` / 短域名解析；必要时保留 `GODEBUG=netdns=cgo` 的开关说明 |
| Docker Desktop 文件共享未包含 `STORAGE_ROOT` / Agent 工作目录 | 创建时 mount denied | 错误信息带原始 stderr + 提示在 Docker Desktop → Settings → Resources → File sharing 添加路径；默认装在 `~` 下通常已共享 |
| 默认镜像 `ubuntu:24.04` 未 pull | 创建失败 | `container_image_missing` 明确提示 `docker pull ubuntu:24.04`；产品明确不自动拉取 |
| 镜像架构与 gtsp 架构不匹配 | `exec format error` | 按镜像 `.Architecture` 选择二进制；`container_not_executable` 明确提示 |
| 默认 root 容器在 Linux 宿主写出的文件属主为 root | 宿主 Agent 后续读写受限 | Docker Desktop（macOS / Windows）通常映射为宿主用户；Linux 属主问题本版不做 `user` 配置（产品未要求），作为已知限制记录 |
| Docker CLI 不在 GUI 进程 PATH | 桌面版找不到 docker | §5.2 常见路径探测 + `docker_binary` 配置 |
| 外部删除 / 后端崩溃留下 running 容器 | 资源占用、状态不一致 | 启动 sweep + 退出三级兜底 + 工具调用时检测；DB 行保留供 Agent 删除 |
| 多个 Agent 同时进入时 Docker 慢 | 单个 turn 等待数秒 | 每 Agent 独立锁；超时上限；不阻塞其他 Agent |
| 自定义宿主 gtsp（`command` 选项）与内置 Linux gtsp 版本不一致 | 容器内工具列表 / 行为差异 | 文档说明容器统一使用内置 Linux 版；自定义 command 场景不保证一致 |
| 应用升级后，升级前创建的容器仍挂载旧 inode 的 gtsp | 新旧 gtsp 协议不兼容时进入握手失败 | 新二进制通过临时文件 + `os.replace` 原子替换；旧容器失败时提示 `delete_container` 后重建 |
| Team 删除 / Agent 离职清理时 Docker 不可用 | DB 记录可删，但容器层可能残留在 Docker 中 | §7.5 仍然删除 DB 行并记录 `container_name` warning，便于人工 `docker rm -f` |
| Windows 容器模式 / WSL 路径 | 不支持 | 只支持 Linux 容器；Windows 使用 Docker Desktop 的 Linux 容器模式 |
| 工具 schema 增加 5 个，TSP Agent prompt 变长 | 轻微 token 增加 | 只有 TSP driver 且 `enabled=true` 时可见；可配置关闭 |
| 外部容器的 gtsp 注入依赖容器内可写 `/tmp` 且有 `mkdir` / `chmod` | distroless 等极简镜像进入失败 | 用 `docker cp` + `docker exec <binary>` 不经 shell，覆盖面已尽量大；仍失败时 `container_not_executable` 明确报错，不做兜底 |
| 外部容器没有 `/workspace/self`，Agent 容易误以为有挂载 | 文件写出容器后丢失 | 进入返回体 `mounted=false` + 明确提示（§8.3）；产品 §5 场景已演示 |
| 容器默认 root、共享宿主内核 | 并非强安全边界 | §10；敏感场景配置 `network=none`、资源限制，必要时 rootless / VM |
| 同一 STORAGE_ROOT 启动多个后端实例 | owner label 相同，可能互相 stop / 删容器 | 设计假设单实例；多实例需在 owner 中加进程 / 实例标识（后续） |
| 进入需 start + gtsp 握手 | 单次进入可能 5–45 秒 | start 后轮询；`enter_timeout_sec` 可调；超时回落宿主并给出原因 |

待定项（实现前可再确认）：

1. Web 属性面板的容器表格具体布局与入口（本次只定义数据契约）；
2. 是否需要为清理失败的残留容器提供管理端「一键清理」入口（当前只记 warning + `container_name`）；
3. 是否在 TUI 显示执行环境；
4. 外部容器是否应支持显式指定 `arch`（当镜像架构与宿主不同时，目前按镜像 `.Architecture` 判断）。

---

## 14. 实施顺序（建议）

1. **gtsp 静态化验证（前置，任务 #105 已进入 REVIEWING）**：构建侧已完成，验收由 gtsp_test 跑四镜像。同时先确认 §5.12 的 0a/0b/0c 三个未验证行为，这三条决定「退出流程」的最终形态。若形态 B 关 stdin 会停掉外部容器，§6.4 与 §8.3 的文案需要调整。
2. **常量 / 配置 / 迁移 / model / DAL**：先把持久层与配置打通，跑迁移测试。
3. **dockerCli + containerService（含 `resolve_target`、`gtspInContainer`）**：fake docker 单测覆盖创建 / 启动 / 停止 / 删除 / 状态 / 目标解析 / 错误分类 / 命令构造。
4. **TSP driver 双 client 与进入退出**：先不接工具，用单测直接调用 `enter_container` / `exit_to_host`。
5. **容器工具 + 注册/可见性/分类**：接 `funcToolService`，更新 schema 快照与分类契约测试（同 commit）。
6. **生命周期**：`backend_main`、`teamService.stop_team_runtime`、Agent 启动不恢复、改名 / 离职 / 删除的容器清理（§7.5）。
7. **controller / WS / 前端**：列表徽标、详情容器表、事件更新、活动 metadata 与徽标、i18n。
8. **打包**：移除 `togo_desktop.spec` 的 Linux gtsp 排除，验证桌面包与 file sharing。
9. **真实 Docker e2e + 产品验收清单**：按 §12.5 / §12.6 逐项跑通。
