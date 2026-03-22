# Agent Driver 可插拔设计

## 目标

把不同 agent 的“执行方式”从 `Agent` 主体中抽离出来，做成可插拔 `driver`，支持：

- 当前的 `native` driver
- 当前的 `claude_sdk` driver
- 后续的 `gemini_cli` / `codex_cli` / 其他外部 agent driver

同时尽量保持以下稳定：

- `schedulerService` 不感知具体 driver
- `roomService` / `messageBus` 不感知具体 driver
- Agent 历史、持久化、状态发布逻辑仍然统一

## 核心思路

这里真正变化的不是 Agent 的“身份”，而是 Agent 的“执行策略”。

- `alice`、`bob`、`researcher` 这些差异主要体现在 prompt、model、历史和房间上下文
- `native`、`claude_sdk`、`gemini_cli` 的差异主要体现在如何执行一轮、如何接入外部系统、如何映射动作

因此更合适的建模方式是：

- `Agent` 负责稳定状态
- `AgentDriver` 负责可替换执行策略

## 运行流程

从调度角度看，链路保持不变：

1. `schedulerService` 收到 `ROOM_AGENT_TURN`
2. scheduler 找到对应 `Agent`
3. scheduler 调用 `agent.consume_task(...)`
4. `Agent.consume_task()` 调用 `agent.run_chat_turn(...)`
5. `Agent.run_chat_turn()` 完成房间绑定与消息同步后，再调用 `self.driver.run_chat_turn(...)`
6. driver 用 `Agent` 暴露的统一能力完成这一轮

也就是说，调度器只依赖 `Agent` 的稳定接口，不依赖具体 driver。

## 代码位置

- [src/service/agentService/core.py](../../src/service/agentService/core.py)
- [src/service/agentService/driver/base.py](../../src/service/agentService/driver/base.py)
- [src/service/agentService/driver/factory.py](../../src/service/agentService/driver/factory.py)
- [src/service/agentService/driver/native.py](../../src/service/agentService/driver/native.py)
- [src/service/agentService/driver/claude_sdk.py](../../src/service/agentService/driver/claude_sdk.py)

## 当前接口

### `AgentDriverConfig`

定义在 [base.py](/Volumes/PData/GitDB/agent_team/src/service/agentService/driver/base.py#L7)。

```python
@dataclass
class AgentDriverConfig:
    driver_type: str
    options: dict[str, Any]
```

职责：

- 保存 driver 类型
- 保存 driver 私有配置
- 作为 factory 的统一输入

### `AgentDriverHost`

定义在 [base.py](/Volumes/PData/GitDB/agent_team/src/service/agentService/driver/base.py#L13)。

它表示 driver 依赖的宿主协议，也就是 driver 可以从 `Agent` 获得哪些能力。

目前主要包含：

- 字段
  - `name`
  - `team_name`
  - `system_prompt`
  - `model`
  - `current_room`
- 方法
  - `sync_room_messages(...)`
  - `chat(...)`
  - `append_history_message(...)`
  - `send_chat_message(...)`
  - `skip_chat_turn()`
  - `make_text_message(...)`

这层协议的价值是：

- driver 不需要知道持久化细节
- driver 不需要知道 scheduler 细节
- driver 只关心“如何把这一轮做完”

### `AgentDriver`

定义在 [base.py](/Volumes/PData/GitDB/agent_team/src/service/agentService/driver/base.py#L45)。

```python
class AgentDriver:
    async def startup(self) -> None: ...
    async def shutdown(self) -> None: ...
    async def run_chat_turn(self, room: ChatRoom, synced_count: int, max_function_calls: int = 5) -> None: ...
```

职责：

- `startup`
  - 初始化 driver 级资源
  - 例如 SDK client、外部进程句柄、会话对象
- `shutdown`
  - 释放 driver 资源
- `run_turn`
  - 驱动某个房间的一轮发言

## `Agent` 壳对象的职责

`Agent` 现在是系统里的稳定入口，定义见 [core.py](/Volumes/PData/GitDB/agent_team/src/service/agentService/core.py#L34)。

`Agent` 负责：

- 基础身份信息
  - `name`
  - `team_name`
  - `model`
  - `system_prompt`
- 生命周期公共状态
  - `wait_task_queue`
  - `status`
  - `current_room`
- 公共历史与持久化
  - `_history`
  - `append_history_message`
  - `dump_history_messages`
  - `inject_history_messages`
- 公共动作桥接
  - `send_chat_message`
  - `skip_chat_turn`
- 通用推理循环能力
  - `chat`
  - `_infer`
  - `_execute_tool`

值得关注的几个方法：

- `run_chat_turn(...)`
  - 统一维护当前房间上下文并先同步消息，再把 `room + synced_count` 交给 driver，见 [core.py](/Volumes/PData/GitDB/agent_team/src/service/agentService/core.py#L133)
- `send_chat_message(...)`
  - 统一处理发消息、跨房间发送、回合结束标记，见 [core.py](/Volumes/PData/GitDB/agent_team/src/service/agentService/core.py#L133)
- `skip_chat_turn()`
  - 统一处理跳过发言逻辑，见 [core.py](/Volumes/PData/GitDB/agent_team/src/service/agentService/core.py#L155)
- `chat(...)`
  - 保留给 `native` driver 使用的通用多轮 function calling 循环，见 [core.py](/Volumes/PData/GitDB/agent_team/src/service/agentService/core.py#L171)

## Factory 设计

factory 位于 [factory.py](/Volumes/PData/GitDB/agent_team/src/service/agentService/driver/factory.py#L10)。

它做两件事：

- `normalize_driver_config(agent_cfg)`
  - 把配置文件里的 agent 配置归一化成 `AgentDriverConfig`
- `build_agent_driver(host, driver_config)`
  - 根据 `driver_type` 创建具体 driver 实例

### 为什么要有 `normalize_driver_config`

因为系统正在从 `runtime` 命名迁移到 `driver` 命名，需要兼容旧配置和新配置。

当前兼容规则：

- 如果配置里有 `driver`
  - 优先使用 `driver.type`
- 如果没有 `driver`，但有 `runtime`
  - 使用 `runtime.type`
- 如果没有 `driver/runtime`，但有 `use_agent_sdk=true`
  - 映射成 `claude_sdk`
- 否则
  - 默认使用 `native`

这样可以做到：

- 新代码和新文档统一使用 `driver`
- 老配置无需立刻批量迁移

## 现有 driver 实现

### Native Driver

文件： [native.py](/Volumes/PData/GitDB/agent_team/src/service/agentService/driver/native.py)

主要逻辑：

- 从房间同步未读消息到历史
- 构造当前轮的 `ChatContext`
- 调用 `Agent.chat(...)`
- 检查本轮是否通过 `send_chat_msg` 或 `skip_chat_msg` 完成
- 如果没完成，注入 reminder 后重试

适合场景：

- 模型接口是 OpenAI-compatible chat completion
- 工具调用由当前系统 `funcToolService` 统一提供
- 历史以 `LlmApiMessage` 为主

### Claude SDK Driver

文件： [claude_sdk.py](/Volumes/PData/GitDB/agent_team/src/service/agentService/driver/claude_sdk.py)

主要逻辑：

- 在 `startup()` 中建立持久 Claude SDK 会话
- 通过 MCP tool 暴露 `send_chat_msg` 和 `skip_chat_msg`
- 每轮把房间增量消息拼成 prompt 发给 SDK
- 监听 SDK 流式消息
- 当工具返回表明“本轮结束”后主动 interrupt

适合场景：

- 需要长期会话状态
- 需要 SDK 自身的 tool / thinking / 多段消息能力
- 更像“外部 Agent 会话”而不是单次 API 调用

## 统一动作设计

不管是哪种 driver，系统认可的核心动作目前只有两种：

- `send_chat_msg`
- `skip_chat_msg`

它们最终都应该映射到 `Agent` 的统一方法：

- `Agent.send_chat_message(...)`
- `Agent.skip_chat_turn()`

这样可以统一：

- 房间写消息的逻辑
- “发到当前房间即本轮结束”的规则
- “跨房间发消息但当前轮未结束”的规则

## 配置建议

建议 agent 配置逐步从旧字段迁移到新字段。

### 旧配置

```json
{
  "name": "alice",
  "model": "claude-sonnet",
  "use_agent_sdk": true,
  "allowed_tools": ["Read", "Write"]
}
```

### 迁移期兼容配置

```json
{
  "name": "alice",
  "model": "claude-sonnet",
  "runtime": {
    "type": "claude_sdk",
    "allowed_tools": ["Read", "Write"],
    "max_turns": 100
  }
}
```

### 推荐新配置

```json
{
  "name": "alice",
  "model": "claude-sonnet",
  "driver": {
    "type": "claude_sdk",
    "allowed_tools": ["Read", "Write"],
    "max_turns": 100
  }
}
```

## 后续新增 `gemini_cli` 的建议

建议新增文件：

- `src/service/agentService/driver/gemini_cli.py`

建议实现：

```python
class GeminiCliAgentDriver(AgentDriver):
    async def startup(self) -> None:
        ...

    async def shutdown(self) -> None:
        ...

    async def run_chat_turn(self, room: ChatRoom, synced_count: int, max_function_calls: int = 5) -> None:
        ...
```

建议配置：

```json
{
  "driver": {
    "type": "gemini_cli",
    "command": ["gemini", "chat", "--json"],
    "env": {
      "GEMINI_API_KEY": "..."
    },
    "timeout_sec": 120
  }
}
```

## 当前限制

这次设计已经把边界拉出来了，但还有一些可以继续收紧的地方：

- `AgentDriverHost` 目前仍暴露了 `_history`、`_turn_ctx` 这种内部字段
- `native` driver 仍然使用了 `Agent.chat(...)` 这套偏现状的执行方式
- “动作协议”还没有被单独抽成通用抽象

## 推荐的后续演进

1. 把 agent 配置逐步切到 `driver.type`
2. 收紧 `AgentDriverHost` 协议，减少 driver 对内部字段的直接依赖
3. 抽出统一的动作协议层
4. 新增 `gemini_cli` driver 并补完整测试
