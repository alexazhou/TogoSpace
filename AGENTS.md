# Agent Team

## 项目概述

多 Agent 聊天室框架，支持多个 LLM Agent 按轮次在聊天室中对话，支持 Function Calling。后端提供 HTTP + WebSocket API，TUI 为终端观察台。

## 技术栈

- Python 3.11+
- tornado（异步 HTTP + WebSocket 服务器）
- pydantic（数据验证）
- textual（TUI 终端界面）
- 兼容 OpenAI API 格式的 LLM 服务（DashScope 等）

## 仓库结构

```
agent_team/
├── src/                 # 后端主程序
├── tui/                 # 终端观察台（独立进程）
├── config/              # 配置文件
│   ├── agents/          # Agent 定义（*.json，每个 agent 一个文件）
│   ├── teams/           # Team 定义（*.json，每个 team 一个文件）
│   └── prompts/         # Agent system prompt（*.md）
├── docs/                # 设计文档
├── logs/                # 运行日志（自动生成）
│   ├── backend/         # 后端日志（v3_chat_<timestamp>.log）
│   └── tui/             # TUI 日志（tui.log，滚动）
├── run/                 # PID 文件（自动生成）
├── scripts/             # 启动/停止脚本
├── tests/               # 测试
├── config.json          # API Key 和服务地址配置
└── requirements.txt     # Python 依赖
```

## 后端目录结构（src/）

```
src/
├── main.py              # 程序入口，初始化所有服务，启动 tornado HTTP 服务器
├── route.py             # HTTP 路由注册（/agents, /rooms, /ws/events）
├── constants.py         # 枚举常量（角色、消息总线 Topic、房间状态等）
├── model/               # 数据定义层
│   ├── api_model.py     # LLM API 数据模型（Message, Tool 等）
│   ├── chat_model.py    # ChatMessage dataclass（聊天室消息）
│   ├── chat_context.py  # 聊天上下文模型
│   ├── agent_event.py   # Agent 事件模型
│   └── web_model.py     # HTTP 响应模型
├── service/             # 有状态服务层（模块级单例）
│   ├── agent_service.py     # Agent 管理（init / close）
│   ├── room_service.py      # 聊天室管理（init / get_room / close_all）
│   ├── scheduler_service.py # 多 Agent 轮次调度（init / run / stop）
│   ├── llm_service.py       # LLM API 调用封装
│   ├── message_bus.py       # 内部消息总线（pub/sub）
│   └── func_tool_service/   # 工具函数服务
│       ├── core.py          # 工具执行入口（init / close）
│       ├── tool_loader.py   # 加载启用的工具函数
│       └── tools.py         # 工具函数实现 + FUNCTION_REGISTRY
├── controller/          # HTTP 控制器层
│   ├── base_controller.py   # 基类
│   ├── agent_controller.py  # GET /agents
│   ├── room_controller.py   # GET /rooms, GET /rooms/{name}/messages
│   └── ws_controller.py     # WebSocket /ws/events（推送实时消息）
└── util/                # 无状态工具层
    ├── config_util.py       # load_agents / load_teams / load_llm_service_config
    └── llm_api_util/        # LLM API 客户端封装
        ├── client.py
        └── models.py
```

## TUI 目录结构（tui/）

```
tui/
├── main.py        # TUI 入口，解析参数，单实例检查，启动 WatcherApp
├── app.py         # Textual App 主类（WatcherApp）
├── widgets.py     # 自定义 Textual 组件
└── api_client.py  # 调用后端 HTTP / WebSocket API
```

## 三层架构规则

设计原则：高内聚低耦合。每层职责单一，层间通过明确接口通信，避免跨层依赖。

| 层 | 可 import | 说明 |
|----|-----------|------|
| `util` | 标准库 + 第三方 | 无状态或无需外部管理状态（内部可有状态），不依赖 model/service |
| `model` | util + 标准库 + 第三方 | 纯数据定义（dataclass/pydantic），不含业务逻辑 |
| `service` | model + util + 标准库 + 第三方 | 有状态业务类，依赖 model 作为数据契约 |

同层之间可以互相引用。禁止下层依赖上层（service 不能被 model/util 引用）。

## 服务启动与停止

### 后端

```bash
# 前台运行（开发调试）
cd src && python main.py [--config-dir config/] [--llm-config config.json] [--port 8080]

# 后台运行（nohup，stdout 写入 logs/backend_stdout.log，运行日志写入 logs/backend/）
./scripts/start_backend.sh [--config-dir ...] [--port ...]

# 停止后台后端（通过 run/backend.pid）
./scripts/stop_backend.sh
```

### TUI 终端观察台

```bash
# 前台运行
./scripts/start_tui.sh [--base-url http://127.0.0.1:8080] [--config config.json]

# 停止（通过 run/tui.pid）
./scripts/stop_tui.sh
```

### 默认端口

| 服务 | 默认端口 | 说明 |
|------|----------|------|
| 后端 HTTP | 8080 | REST API + WebSocket，可通过 `--port` 覆盖 |
| TUI | — | 无监听端口，连接后端 8080 |

### PID 文件

两个进程均有单实例保护，运行时 PID 写入 `run/backend.pid` / `run/tui.pid`，进程退出后自动删除。

## 工作目录约定

`main.py` 启动时调用 `os.chdir(os.path.dirname(os.path.abspath(__file__)))` 将工作目录固定为 `src/`。
所有相对路径（如配置文件、prompt 文件）均以 `src/` 为基准。
测试和其他入口脚本若需要读取这些文件，须自行保证工作目录正确，或使用绝对路径。

## 配置文件
### Agent + Team 两级配置

配置拆分为 Agent 定义和 Team 定义两个独立概念：

- **Agent 定义** (`config/agents/<name>.json`)：全局共享 de Agent 属性（prompt/model）
- **Team 定义** (`config/teams/<name>.json`)：包含一个或多个 group（聊天室），每个 Team 中的 Agent 实例相互隔离

#### Agent 定义示例

```json
{
  "name": "alice",
  "system_prompt": "...",
  "model": "glm-4.7"
}
```

#### Team 定义示例

```json
{
  "name": "default",
  "groups": [
...
    {
      "name": "general",
      "type": "group",
      "members": ["alice", "bob"],
      "initial_topic": "大家好！",
      "max_turns": 6
    }
  ],
  "max_function_calls": 5
}
```

#### 数据隔离规则

- Agent 实例 key：`agent_name@team_name`（如 `alice@default`）
- Room key：`room_name@team_name`（如 `general@default`）
- 同一 Agent 在不同 Team 拥有独立的对话历史

### 其他配置

| 文件 | 说明 |
|------|------|
| `config.json` | LLM 服务配置（API Key、base_url、active_llm_service） |

## 终端模拟器 (Terminal Simulator)

[go_simu_terminal](https://github.com/alexazhou/go_simu_terminal) 是一个"无头"终端模拟器，能将终端内容实时渲染为 **PNG** 或 **SVG**，内置 HTTP 控制接口，适合 AI Agent 操作 TUI。详见 [docs/go_simu_terminal.md](docs/go_simu_terminal.md)。

`simu_terminal_go` 已安装到系统 PATH，可直接使用。

```bash
# 运行 TUI（确保后端在运行且停止已有 TUI）
simu_terminal_go --port 8889 -- .venv/bin/python tui/main.py --base-url http://127.0.0.1:8080

# 截图 - PNG（可直接用 Read tool 读取图片）
curl "http://localhost:8889/screenshot?format=png&scale=2" -o screenshot.png

# 发送按键 / 文本
curl -X POST http://localhost:8889/input -H 'Content-Type: application/json' -d '{"key":"tab"}'
curl -X POST http://localhost:8889/input -H 'Content-Type: application/json' -d '{"text":"hello\n"}'
```

## 新增工具函数

在 `src/util/functions.py` 中添加函数，并注册到 `FUNCTION_REGISTRY`。
若函数需要访问聊天室上下文（`chat_room`、`agent_name`），设置 `func.needs_context = True`，参数以 `_` 前缀命名（会被自动注入，不会暴露给 LLM）。
