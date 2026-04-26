# TogoSpace

## 项目概述

多 Agent 聊天室框架，支持多个 LLM Agent 按轮次对话与 Function Calling。后端提供 HTTP + WebSocket API。当前包含两个前端：终端前端 `tui/` 与 Web 前端 `frontend/`。

## 技术栈

- Python 3.11+（项目使用 `.venv` 虚拟环境）
- tornado（异步 HTTP + WebSocket）
- pydantic（模型校验）
- textual（TUI）
- litellm（兼容多种 LLM API 服务）

## 仓库结构

```text
TogoSpace/
├── src/                     # 后端
│   ├── controller/          # HTTP / WebSocket 接口层
│   ├── service/             # 业务逻辑层
│   │   ├── agentService/    # Agent 核心逻辑（driver、调度、历史）
│   │   ├── funcToolService/ # Function Calling 工具
│   │   ├── roomService.py   # 房间管理
│   │   ├── schedulerService.py  # 调度器
│   │   ├── llmService.py    # LLM 调用
│   │   └── ...              # 其他服务模块
│   ├── dal/                 # 数据访问层
│   ├── model/               # 数据定义
│   │   ├── coreModel/       # 核心模型（AgentEvent、ChatModel、WebModel）
│   │   ├── dbModel/         # 数据库模型（Agent、Room、Team 等）
│   │   └── ...              # 其他模型文件
│   ├── util/                # 通用工具
│   ├── backend_main.py      # 后端入口
│   ├── appEntry.py          # 托盘模式启动入口
│   ├── appPaths.py          # 运行时路径配置
│   ├── constants.py         # 常量定义
│   ├── route.py             # 路由配置
│   └── ...                  # 其他模块文件
├── frontend/                # Web 前端（Git Submodule）
│   ├── src/                 # Vue 3 + TypeScript 源码
│   ├── public/              # 静态资源
│   ├── scripts/             # 构建脚本
│   ├── package.json         # 依赖配置
│   └── vite.config.ts       # Vite 配置
├── tui/                     # 终端前端（Textual）
├── assets/                  # 静态资源（preset、icons、execute）
├── docs/                    # 设计与规范文档
├── scripts/                 # 启停脚本、构建脚本
├── tests/                   # 测试（unit / integration / api）
├── dev_storage_root/        # 开发模式运行数据（自动生成）
└── ...                      # 其他根目录文件
```

## 四层架构规则

设计原则：高内聚、低耦合。

| 层 | 可 import | 说明 |
|----|-----------|------|
| `controller` | `service` + `dal` + `model` + `util` + 标准库 + 第三方 | 接口层（HTTP / WebSocket），负责请求编排与响应 |
| `service` | `dal` + `model` + `util` + 标准库 + 第三方 | 有状态业务逻辑 |
| `dal` | `model` + `util` + 标准库 + 第三方 | 数据访问层，封装数据库操作 |
| `model` | `util` + 标准库 + 第三方 | 数据定义，不写业务流程 |
| `util` | 标准库 + 第三方 | 通用工具，不依赖 `model/service` |

同层可互相引用。禁止下层反向依赖上层：`controller -> service -> dal -> model -> util`。

## 开发约定

### 接口实现约定
- **后端接口返回的数据，应尽量复用已有的数据对象，并且复用自动序列化能力，不要手动把对象字段拿出来再拼 json。**
- **后端接口不要为了前端方便，拼接一些兼容或不必要的字段，只要返回符合业务逻辑的数据即可，展示的问题由前端解决。**

### Git 约定

**Git 工具约定**
- 使用 `scripts/commit_and_push_frondbackend.py` 统一管理前后端 Git 操作
- 该脚本会自动处理前端 submodule 切换分支、同步远端、提交、推送等操作
- 示例用法：
  ```bash
  # 查看前后端状态
  python scripts/commit_and_push_frondbackend.py --action status

  # 提交前后端改动
  python scripts/commit_and_push_frondbackend.py --action commit -m "fix: description"

  # 同步 + 提交 + 推送（常用流程）
  python scripts/commit_and_push_frondbackend.py --action sync,commit,push -m "fix: description"

  # 仅处理前端或后端
  python scripts/commit_and_push_frondbackend.py --action status --target frontend
  python scripts/commit_and_push_frondbackend.py --action commit -m "fix: description" --target backend
  ```

**提交规范**
- 开发完成后不要自动提交，等待用户明确要求「提交」或「commit」后再执行
- commit message 不要加 `Co-Authored-By` 行，不要署名 AI Agent

**前端子模块**
- 在 `frontend/` 子模块内提交时，必须先切换到 master 分支，禁止在 detached HEAD 状态下提交
- 提交后端时若前端有新 commit，需同步更新子模块指针：`git add frontend && git commit`

**冲突处理**
- 禁止擅自使用 `git checkout --theirs .` 或 `git checkout --ours .` 放弃改动
- 冲突发生时必须询问用户处理方式（手动合并、保留本地、使用远程等）
- `git stash` 后若冲突无法自动合并，不要执行 `git stash drop`，保留 stash 直到用户确认

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

## 文档索引（docs/）

### 项目级

- docs/ROADMAP.md：里程碑与阶段目标
- docs/go_simu_terminal.md：终端模拟器使用说明

### 代码规范

- docs/code_rule/common_coding_rule.md
- docs/code_rule/enum_conventions.md
- docs/code_rule/logger_convention.md
- docs/code_rule/import_convention.md

### 技术设计

- docs/tech/01_architecture/：系统架构概述、服务依赖关系
- docs/tech/02_mvc/：Controller / Service / DAL 开发规范
- docs/tech/03_dal/：DAL相关
- docs/tech/04_agent/：Driver 架构、调度逻辑、任务生命周期、状态持久化、Token 压缩
- docs/tech/05_llm/：LLM 配置指南、Prompt Cache
- docs/tech/06_i18n/：国际化设计方案、Web 实体 i18n 方案
- docs/tech/07_test/：测试执行架构、测试用例设计指南
- docs/tech/08_debugging/：轮次与消息排查指南
- docs/tech/09_tui/：TUI 布局方案
- docs/tech/10_release/：桌面打包发布方案、演示模式只读方案、未初始化场景调度闸门

### 版本文档

- docs/versions/版本文档规范.md：版本管理、文档命名规范
- docs/versions/v*/：按版本沉淀的产品、技术、任务文档

### 版本发布

- docs/RELEASE_HANDBOOK.md：版本发布操作手册

相关工具：
- scripts/build_mac.py：构建 macOS app（开发调试用）
- scripts/build_release.py：构建 + 签名 + 公证 + 打包（发布用）
- scripts/build_config.json：本地发布配置（从 build_config.json.example 复制填写）
