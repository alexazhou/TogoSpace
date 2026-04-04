# Agent Team

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
agent_team/
├── src/                 # 后端
├── tui/                 # 终端前端（Textual）
├── frontend/            # Web 前端（Vue 3 + Vite + TypeScript，Git Submodule）
├── config/              # 运行配置（role_templates/ teams/ setting.json）
├── docs/                # 设计与规范文档
├── logs/                # 运行日志（自动生成）
│   ├── backend/
│   └── tui/
├── run/                 # PID 文件（自动生成）
├── scripts/             # 启停脚本
├── tests/               # 测试
└── data/                # 运行数据（SQLite 等）
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

- **代码提交**：开发完成后不要自动提交代码。统一等待用户明确要求「提交」或「commit」后再执行 git commit/push。

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
.venv/bin/python3 tui/tui_main.py [--base-url http://127.0.0.1:8080] [--config config/setting.json]

# 或使用脚本
./scripts/start_tui.sh [--base-url http://127.0.0.1:8080] [--config config/setting.json]

# 停止
./scripts/stop_tui.sh
```

### 测试

```bash
# 快速跑所有测试（默认并行，无覆盖率）
./scripts/run_tests.sh

# 跑覆盖率测试
./scripts/run_tests.sh --cov

# 指定目录或用例（支持所有 pytest 参数）
./scripts/run_tests.sh tests/unit
./scripts/run_tests.sh -k "test_name"

# 调试模式（串行运行）
./scripts/run_tests.sh --serial
```

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

## 日志说明（已更新）

后端日志已按模块拆分并保留全局日志：

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

- RoleTemplate 定义：`config/role_templates/*.json`
- Team 定义：`config/teams/*.json`
- 运行配置：`config/setting.json`
  - `llm_services` / `default_llm_server`
  - `persistence`

默认 `--config-dir` 未指定时，回退到仓库内 `config/`。

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
- [docs/controller_development.md](docs/controller_development.md)：Controller 开发说明
- [docs/go_simu_terminal.md](docs/go_simu_terminal.md)：终端模拟器使用说明

### 代码规范

- [docs/code_rule/service_conventions.md](docs/code_rule/service_conventions.md)
- [docs/code_rule/logger_convention.md](docs/code_rule/logger_convention.md)
- [docs/code_rule/import_convention.md](docs/code_rule/import_convention.md)
- [docs/code_rule/formatting_convention.md](docs/code_rule/formatting_convention.md)

### 技术设计

- [docs/tech/agent_driver_architecture.md](docs/tech/agent_driver_architecture.md)
- [docs/tech/agent_driver_vs_subclass.md](docs/tech/agent_driver_vs_subclass.md)
- [docs/tech/agent_scheduling_logic.md](docs/tech/agent_scheduling_logic.md)
- [docs/tech/service_dependencies.md](docs/tech/service_dependencies.md)
- [docs/tech/state_persistence.md](docs/tech/state_persistence.md)
- [docs/tech/testing_architecture.md](docs/tech/testing_architecture.md)
- [docs/tech/tui_layout.md](docs/tech/tui_layout.md)

### 版本文档

- `docs/versions/v*/`：按版本沉淀的产品、技术、任务文档（v1 ~ v9）
