# TogoAgent

## 项目概述

多 Agent 聊天室框架，支持多个 LLM Agent 按轮次对话与 Function Calling。后端提供 HTTP + WebSocket API。当前包含两个前端：终端前端 `tui/` 与 Web 前端 `frontend/`。

## 技术栈

- Python 3.11+（项目使用 `.venv` 虚拟环境）
- tornado（异步 HTTP + WebSocket）
- pydantic（模型校验）
- textual（TUI）
- 兼容 OpenAI API 形态的模型服务

## 仓库结构（当前）

```text
TogoAgent/
├── src/                 # 后端
├── tui/                 # 终端前端（Textual）
├── frontend/            # Web 前端（Vue 3 + Vite + TypeScript，Git Submodule）
├── assets/              # 静态资源（preset、icons、execute、prompts）
├── docs/                # 设计与规范文档
├── scripts/             # 启停脚本、构建脚本
├── tests/               # 测试
├── run/                 # PID 文件（自动生成）
└── dev_storage_root/    # 开发模式运行数据（自动生成，不提交）
    ├── setting.json
    ├── data/
    ├── logs/
    └── workspace/
```

## 后端目录结构（src/）

```text
src/
├── backend_main.py
├── route.py
├── constants.py
├── controller/
│   ├── baseController.py
│   ├── roleTemplateController.py
│   ├── agentController.py
│   ├── roomController.py
│   ├── teamController.py
│   └── wsController.py
├── service/
│   ├── roomService.py
│   ├── schedulerService.py
│   ├── messageBus.py
│   ├── llmService.py
│   ├── ormService.py
│   ├── persistenceService.py
│   ├── teamService.py
│   ├── roleTemplateService.py
│   ├── agentService/
│   │   ├── core.py
│   │   ├── agent.py
│   │   ├── agentTaskConsumer.py
│   │   ├── agentTurnRunner.py
│   │   ├── agentHistoryStore.py
│   │   ├── promptBuilder.py
│   │   ├── toolRegistry.py
│   │   └── driver/
│   └── funcToolService/
│       ├── core.py
│       ├── toolLoader.py
│       └── tools.py
├── model/
├── dal/
└── util/
```

## TUI 目录结构（tui/）

```text
tui/
├── tui_main.py
├── app.py
├── app.tcss
├── widgets.py
└── api_client.py
```

## Web 前端目录结构（frontend/）

```text
frontend/
├── src/
├── public/
├── scripts/
├── package.json
├── vite.config.ts
└── README.md
```

## 四层架构规则

设计原则：高内聚、低耦合。

| 层 | 可 import | 说明 |
|----|-----------|------|
| `controller` | `service` + `model` + `util` + 标准库 + 第三方 | 接口层（HTTP / WebSocket），负责请求编排与响应 |
| `service` | `model` + `util` + 标准库 + 第三方 | 有状态业务逻辑 |
| `model` | `util` + 标准库 + 第三方 | 数据定义，不写业务流程 |
| `util` | 标准库 + 第三方 | 通用工具，不依赖 `model/service` |

同层可互相引用。禁止下层反向依赖上层：`controller -> service -> model -> util`。

## 开发约定

### 接口实现约定
- **后端接口返回的数据，应尽量复用已有的数据对象，并且复用自动序列化能力，不要手动把对象字段拿出来再拼 json。**
- **后端接口不要为了前端方便，拼接一些兼容或不必要的字段，只要返回符合业务逻辑的数据即可，展示的问题由前端解决。**

### 提交约定
- **代码提交**：开发完成后不要自动提交代码。统一等待用户明确要求「提交」或「commit」后再执行 git commit/push。
- **commit message**：提交信息中不要加 `Co-Authored-By` 行，不要署名 AI Agent。
- **提交脚本**：使用 `scripts/commit_and_push_frondbackend.py` 统一提交前后端代码：
    ```bash
    python scripts/commit_and_push_frondbackend.py --action sync,commit,push --target all -m "fix: description"
    ```
    常见用法：
    ```bash
    python scripts/commit_and_push_frondbackend.py --action status --target all
    python scripts/commit_and_push_frondbackend.py --action commit --target all -m "fix: description"
    python scripts/commit_and_push_frondbackend.py --action push --target all
    python scripts/commit_and_push_frondbackend.py --action sync --target frontend
    ```
    该脚本会按 `--action` 显式执行：前端切换 master 分支；`status` 查看前后端仓库状态；`sync` 仅做 fast-forward 拉取；`commit` 只做 add/commit；`push` 只推送已有本地提交。
- **前端子模块提交**：在 `frontend/` 子模块内提交代码时，必须先切换到 master 分支，禁止在 detached HEAD（悬空版本）状态下提交，否则提交会丢失。
- **前端子模块同步**：提交后端代码时，若发现前端子模块（`frontend/`）有新的 commit，需同步更新后端仓库中的子模块指针版本：
    ```bash
    cd frontend && git pull origin master
    cd .. && git add frontend && git commit -m "chore: update frontend submodule"
    ```

### 拉取约定
- **前端子模块同步**：拉取后端代码后，若前端子模块指针发生变化：
    - **可 fast-forward**：直接执行 `git submodule update --init --recursive` 拉取更新
    - **不可 fast-forward**：询问用户处理方式（如 stash 本地改动、强制覆盖等）

### Git 冲突处理约定
- **禁止擅自放弃本地改动**：当 `git stash pop` 或 `git merge` 产生冲突时，严禁使用 `git checkout --theirs .` 或 `git checkout --ours .` 直接放弃一方改动。
- **必须询问用户**：冲突发生时，应向用户说明冲突内容（哪些文件、冲突类型），询问用户如何处理：
    - 手动合并
    - 保留本地改动
    - 使用远程版本
    - 其他处理方式
- **谨慎处理 stash**：`git stash` 后若冲突无法自动合并，不要执行 `git stash drop`，应保留 stash 直到用户确认处理方式。

## Compact 约定（当前实现）

- 相关实现位于 `src/service/agentService/agentTurnRunner.py`、`src/service/agentService/compact.py`、`src/service/agentService/agentHistoryStore.py`、`src/model/dbModel/historyUsage.py`、`src/service/persistenceService.py`。
- `_check_compact(...)` 会在两处触发：
  - 推理请求发起前，基于估算 token 执行 `pre-check`
  - assistant 响应成功写入 history 之后，基于实际 `usage.prompt_tokens` 优先执行 `post-check`
- `pre-check`、`post-check`、overflow retry 中的 compact 一旦失败，当前实现都会直接抛异常；不要再按"失败后继续推理"的旧语义修改代码或补测试。
- `agent_histories.usage` 中用 `compact_stage` 记录本轮 compact 阶段，取值为 `none`、`pre`、`post`：
  - `none`：本轮未触发 compact
  - `pre`：请求发出前触发了 compact
  - `post`：assistant 已成功写入 history，随后触发了 post-check compact
- post-check 发生在 assistant 已提交之后，因此 post-check compact 失败时，history 中会保留本次 assistant 的 `SUCCESS` 记录，但调用栈仍会继续抛异常给上层。
- runtime 内存窗口与启动恢复窗口都以最新 `COMPACT_SUMMARY` 为边界：只保留该摘要及其之后的消息。

## 启动与停止

### 后端

```bash
# 前台运行（开发）
.venv/bin/python3 src/backend_main.py [--config-dir config] [--port 8080]

# 后台运行
./scripts/start_backend.sh [--config-dir ...] [--port ...]

# 停止后台后端
./scripts/stop_backend.sh
```

### TUI 前端

```bash
# 前台运行
.venv/bin/python3 tui/tui_main.py [--base-url http://127.0.0.1:8080] [--config ~/.togo_agent/setting.json]

# 或使用脚本
./scripts/start_tui.sh [--base-url http://127.0.0.1:8080] [--config ~/.togo_agent/setting.json]

# 停止
./scripts/stop_tui.sh
```

### 测试

```bash
# 快速跑默认测试（默认并行，无覆盖率，仅 unit + integration）
./scripts/run_tests.sh

# 跑覆盖率测试
./scripts/run_tests.sh --cov

# 指定目录或用例（支持所有 pytest 参数）
./scripts/run_tests.sh tests/unit
./scripts/run_tests.sh -k "test_name"

# 调试模式（串行运行）
./scripts/run_tests.sh --serial

# 运行 API 测试（需要启动后端进程，不在默认范围内）
./scripts/run_tests.sh tests/api
```

- 默认测试范围：`tests/unit` + `tests/integration`，不含 `tests/api`。
- API 测试会启动真实后端子进程和 Mock LLM 服务，执行时间较长，需手动指定运行。
- 全量测试执行时间约 15-20 秒，超时时间设置 30 秒即可。
- 若在沙盒环境中运行 `tests/api/` 下的 API 测试，通常需要先申请提权。
- 原因：这类测试会启动本地 mock LLM / HTTP 服务并绑定 `127.0.0.1` 端口，沙盒内可能因端口绑定受限而失败。

### Web 前端

```bash
cd frontend
npm install
npm run dev
```

默认通过 Vite 代理连接 `http://127.0.0.1:8080`。如需指定后端地址，可用：

```bash
cd frontend
VITE_API_BASE_URL=http://127.0.0.1:8080 npm run dev
```

## 日志说明

后端日志位于 `STORAGE_ROOT/logs/backend/`，已按模块拆分并保留全局日志：

- 全局：`logs/backend/backend.log`
- 全局告警：`logs/backend/backend_warning.log`
- 模块拆分：`logs/backend/service/*.log`、`logs/backend/controller/*.log`、`logs/backend/util/*.log`、`logs/backend/dal/*.log`

当前策略：

- `service.agentService` / `service.roomService` / `service.schedulerService` 设为全局可见（既写分拆文件，也进入全局日志）
- 其他模块按 `global` 配置决定是否进入全局日志
- 全部采用 `RotatingFileHandler`（100MB，保留 3 份）

## 工作目录约定

`backend_main.py` 在启动后会 `chdir` 到 `src/`。仓库内相对路径读取逻辑以此为基准。

## 配置文件约定

配置分为两类，路径与版本控制策略不同：

| 类别 | 内容 | 路径 | 版本控制 |
|------|------|------|----------|
| **preset**（预置内容） | RoleTemplate / Team | `assets/preset/role_templates/*.json`、`assets/preset/teams/*.json` | 是，随源码提交 |
| **config**（运行配置） | LLM 服务、API key、persistence 等 | `setting.json`（位于 storage_root） | 否，用户私有 |

- `preset/` 固定由代码自动查找，不可通过参数指定。
- 运行配置默认读取 `storage_root/setting.json`；可用 `--config-dir <dir>` 指定其他目录（目录下需有 `setting.json`）。

## 目录路径约定

所有可写目录统一由 `STORAGE_ROOT` 管理：

| 运行模式 | STORAGE_ROOT | 说明 |
|----------|--------------|------|
| **打包模式**（PyInstaller frozen） | `~/.togo_agent/` | 用户私有数据目录 |
| **开发模式**（直接运行代码） | `repo/dev_storage_root/` | 仓库本地开发目录 |

目录结构（基于 STORAGE_ROOT）：

```text
STORAGE_ROOT/
├── setting.json        # 运行配置
├── data/               # SQLite 数据库
├── logs/
│   └── backend/        # 后端日志
└── workspace/          # Agent 工作目录
```

- 开发模式下的 `dev_storage_root/` 已加入 `.gitignore`，不会提交到仓库。
- 静态资源（`assets/`）不纳入 STORAGE_ROOT：打包时位于 `_MEIPASS`，开发时位于仓库 `assets/`。

## 前端仓库说明（双前端）

- `tui/`：仓库内原生终端前端，适合本地排障、终端观察和自动化终端操作。
- `frontend/`：Web 前端子仓库（Git Submodule，见 `.gitmodules`），基于 Vue 3 + Vite + TypeScript，面向浏览器使用场景。
- 两个前端都消费同一套后端 API（HTTP + WebSocket），功能目标保持一致，交互形态不同。

## 工具函数扩展（当前实现）

在 `src/service/funcToolService/tools.py` 新增函数，并注册到 `FUNCTION_REGISTRY`。

若函数需要上下文注入（如当前房间、agent、team），使用 `_context` 参数（由工具层注入，不暴露给 LLM）。

## 文档索引（docs/）

### 项目级

- [docs/ROADMAP.md](docs/ROADMAP.md)：里程碑与阶段目标
- [docs/文档规范.md](docs/文档规范.md)：文档书写规范
- [docs/mvc/controller_development.md](docs/mvc/controller_development.md)：Controller 开发说明
- [docs/go_simu_terminal.md](docs/go_simu_terminal.md)：终端模拟器使用说明

### 代码规范

- [docs/code_rule/common_coding_rule.md](docs/code_rule/common_coding_rule.md)
- [docs/code_rule/enum_conventions.md](docs/code_rule/enum_conventions.md)
- [docs/code_rule/logger_convention.md](docs/code_rule/logger_convention.md)
- [docs/code_rule/import_convention.md](docs/code_rule/import_convention.md)
- [docs/mvc/service_conventions.md](docs/mvc/service_conventions.md)
- [docs/mvc/dal_conventions.md](docs/mvc/dal_conventions.md)

### 技术设计

- [docs/tech/agent_driver_architecture.md](docs/tech/agent_driver_architecture.md)
- [docs/tech/agent_driver_vs_subclass.md](docs/tech/agent_driver_vs_subclass.md)
- [docs/tech/agent_scheduling_logic.md](docs/tech/agent_scheduling_logic.md)
- [docs/tech/token_compaction.md](docs/tech/token_compaction.md)
- [docs/tech/task_lifecycle.md](docs/tech/task_lifecycle.md)
- [docs/tech/turn_runner_as_driver_host.md](docs/tech/turn_runner_as_driver_host.md)
- [docs/tech/service_dependencies.md](docs/tech/service_dependencies.md)
- [docs/tech/state_persistence.md](docs/tech/state_persistence.md)
- [docs/tech/test_execution_architecture.md](docs/tech/test_execution_architecture.md)
- [docs/tech/test_case_design_guide.md](docs/tech/test_case_design_guide.md)
- [docs/tech/tui_layout.md](docs/tech/tui_layout.md)

### 版本文档

- `docs/versions/v*/`：按版本沉淀的产品、技术、任务文档（v1 ~ v10）

### 版本发布

- [docs/RELEASE_HANDBOOK.md](docs/RELEASE_HANDBOOK.md)：版本发布操作手册

相关工具：
- `scripts/build_mac.py`：构建 macOS app（开发调试用）
- `scripts/build_release.py`：构建 + 签名 + 公证 + 打包（发布用）
- `scripts/build_config.json`：本地发布配置（从 `build_config.json.example` 复制填写）
