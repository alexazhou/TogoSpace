# Agent Team

## 项目概述

多 Agent 聊天室框架，支持多个 LLM Agent 按轮次在聊天室中对话，支持 Function Calling。

## 技术栈

- Python 3.11+
- aiohttp（异步 HTTP）
- pydantic（数据验证）
- DashScope API（阿里云 LLM 服务）

## 目录结构

```
src/
├── main.py              # 程序入口
├── model/               # 数据定义层
│   ├── api_model.py     # Pydantic API 数据模型（Message, Tool, ChatCompletionRequest 等）
│   └── chat_model.py    # ChatMessage dataclass（聊天室消息）
├── service/             # 有状态服务层
│   ├── agent.py         # Agent 类（生成回复、支持 Function Calling）
│   ├── api_client.py    # APIClient 类（异步 HTTP 调用 DashScope API）
│   ├── chat_room.py     # ChatRoom 类（管理聊天记录和上下文）
│   ├── function_service.py  # build_tools / execute_function
│   └── scheduler.py     # Scheduler 类（多 Agent 轮次调度）
└── util/                # 无状态工具层
    ├── config.py        # setup_logger / load_config / load_prompt / load_api_key
    ├── function_loader.py   # load_enabled_functions / python_type_to_json_schema / get_function_metadata
    └── functions.py     # 工具函数实现 + FUNCTION_REGISTRY
```

## 三层架构规则

| 层 | 可 import | 说明                                |
|----|-----------|-----------------------------------|
| `util` | 标准库 + 第三方 | 无状态(或者无需外部管理状态)，不依赖 model/service |
| `model` | util + 标准库 + 第三方 | 纯数据定义（dataclass/pydantic）         |
| `service` | model + util + 标准库 + 第三方 | 有状态类                              |

同层之间可以互相引用。禁止下层依赖上层（service 不能被 model/util 引用）。

## 运行

```bash
cd src && python main.py
```

## 工作目录约定

`main.py` 启动时调用 `os.chdir(os.path.dirname(os.path.abspath(__file__)))` 将工作目录固定为 `src/`。
所有相对路径（如配置文件、prompt 文件）均以 `src/` 为基准。
测试和其他入口脚本若需要读取这些文件，须自行保证工作目录正确，或使用绝对路径。

## 配置文件

| 文件 | 说明 |
|------|------|
| `config/agents_v2.json` | Agent 配置（名称、模型、prompt 文件路径、聊天室设置、最大轮次） |
| `config.json` | API Key（`anthropic.api_key` 字段） |
| `resource/bk/function_list.json` | 启用的函数列表（`enabled_functions` 字段） |

## 新增工具函数

在 `src/util/functions.py` 中添加函数，并注册到 `FUNCTION_REGISTRY`。
若函数需要访问聊天室上下文（`chat_room`、`agent_name`），设置 `func.needs_context = True`，参数以 `_` 前缀命名（会被自动注入，不会暴露给 LLM）。
