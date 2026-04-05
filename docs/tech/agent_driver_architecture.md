# Agent Driver 可插拔设计

## 目标

把不同 agent 的"执行方式"从核心逻辑中抽离出来，做成可插拔 `driver`，支持：

- 当前的 `native` driver
- 当前的 `claude_sdk` driver
- 后续的 `gemini_cli` / `codex_cli` / 其他外部 agent driver

同时尽量保持以下稳定：

- `schedulerService` 不感知具体 driver
- `roomService` / `messageBus` 不感知具体 driver
- Agent 历史、持久化、状态发布逻辑仍然统一

## 核心思路

这里真正变化的不是 Agent 的"身份"，而是 Agent 的"执行策略"。

- `alice`、`bob`、`researcher` 这些差异主要体现在 prompt、model、历史和房间上下文
- `native`、`claude_sdk`、`gemini_cli` 的差异主要体现在如何执行一轮、如何接入外部系统、如何映射动作

因此更合适的建模方式是：

- `Agent` 负责对外 facade
- `AgentDriver` 负责可替换执行策略
- `AgentTurnRunner` 负责 turn 级逻辑与 driver 宿主能力

## 组件关系

```
Agent (facade)
 └── task_consumer: AgentTaskConsumer (任务管道)
      ├── 拥有: status, current_db_task, 消费协程
      └── _turn_runner: AgentTurnRunner (内部创建，实现 AgentDriverHost)
           ├── gt_agent, system_prompt, agent_workdir, max_function_calls
           ├── _history: AgentHistoryStore    (自建)
           ├── tool_registry: AgentToolRegistry  (自建)
           ├── driver: AgentDriver            (自建，host=self)
           └── _infer(), _execute_tool()      (Driver 回调)
```

## 运行流程

从调度角度看，链路是：

1. `schedulerService` 收到 `ROOM_AGENT_TURN`
2. scheduler 找到对应 `Agent`
3. scheduler 创建数据库任务并调用 `agent.start_consumer_task()`
4. `AgentTaskConsumer.consume()` 认领并执行任务
5. Consumer 调用 `turn_runner.run_chat_turn(task)`
6. TurnRunner 完成房间消息同步后，调用 `self.driver.run_chat_turn(task, synced_count)`
7. driver 用 TurnRunner 暴露的宿主能力完成这一轮

调度器只依赖 `Agent` 的稳定接口，不感知具体 driver 或内部组件。

## 代码位置

- `src/service/agentService/agent.py`
- `src/service/agentService/agentTaskConsumer.py`
- `src/service/agentService/agentTurnRunner.py`
- `src/service/agentService/core.py`
- `src/service/agentService/driver/base.py`
- `src/service/agentService/driver/factory.py`
- `src/service/agentService/driver/nativeDriver.py`
- `src/service/agentService/driver/claudeSdkDriver.py`

## 当前接口

### `AgentDriverConfig`

定义在 `driver/base.py`。

```python
@dataclass
class AgentDriverConfig:
    driver_type: DriverType = DriverType.NATIVE
    options: dict[str, Any] = field(default_factory=dict)
```

职责：

- 保存 driver 类型
- 保存 driver 私有配置
- 作为 factory 的统一输入

### `AgentDriverHost`

定义在 `driver/base.py`。

它表示 driver 依赖的宿主协议。当前宿主是 `AgentTurnRunner` 实例（不是 `Agent`）。

```python
class AgentDriverHost(Protocol):
    gt_agent: GtAgent
    system_prompt: str
    agent_workdir: str
    _history: AgentHistoryStore
    tool_registry: AgentToolRegistry

    async def _infer(self, tools: Optional[list[OpenAITool]]) -> OpenAIMessage: ...
    async def _execute_tool(self) -> None: ...
```

各 driver 对 host 的依赖情况：

| 访问项 | nativeDriver | tspDriver | claudeSdkDriver |
|--------|:---:|:---:|:---:|
| `host.gt_agent` | — | ✓ | ✓ |
| `host.system_prompt` | — | — | ✓ |
| `host.agent_workdir` | — | ✓ | — |
| `host._history` | — | — | ✓ |
| `host.tool_registry` | ✓ | ✓ | ✓ |
| `host._infer()` | — | — | — |
| `host._execute_tool()` | — | — | ✓ |

这层协议的价值是：

- driver 不需要知道持久化细节
- driver 不需要知道 scheduler 细节
- driver 只关心"如何把这一轮做完"

### `AgentDriver`

定义在 `driver/base.py`。

```python
class AgentDriver:
    def __init__(self, host: AgentDriverHost, config: AgentDriverConfig):
        self.host = host
        self.config = config

    async def startup(self) -> None: ...
    async def shutdown(self) -> None: ...
    async def run_chat_turn(self, task: GtAgentTask, synced_count: int) -> None: ...
```

职责：

- `startup`：初始化 driver 级资源（SDK client、外部进程句柄、会话对象）
- `shutdown`：释放 driver 资源
- `run_chat_turn`：驱动某个任务的一轮发言

## `AgentTurnRunner` 的职责

`AgentTurnRunner` 是 driver 的宿主（host），定义在 `agentTurnRunner.py`。

TurnRunner 负责：

- Turn 级资源管理（自建 driver、tool_registry、_history）
- 房间消息同步（`pull_room_messages_to_history`）
- 推理调用（`_infer`）
- 工具调用编排（`_execute_tool`、`_dispatch_tool_calls`）
- 执行 turn 主循环并调用 driver

TurnRunner 构造时只接收值类型参数：

```python
class AgentTurnRunner:
    def __init__(
        self, *,
        gt_agent: GtAgent,
        system_prompt: str,
        agent_workdir: str = "",
        max_function_calls: int = 5,
        driver_config: AgentDriverConfig | None = None,
    ):
        self._history = AgentHistoryStore(gt_agent.id or 0)
        self.tool_registry = AgentToolRegistry()
        self.driver = build_agent_driver(self, driver_config or AgentDriverConfig())
```

## `Agent` facade 的职责

`Agent` 是纯 facade，定义在 `agent.py`，对外只暴露高层接口：

- **生命周期**：`startup()` / `close()`
- **任务管理**：`start_consumer_task()` / `stop_consumer_task()` / `resume_failed()`
- **历史操作**：`dump_history_messages()` / `inject_history_messages()`
- **状态属性**：`status` / `current_db_task` / `is_active`

Agent 内部只持有 `gt_agent`、`system_prompt` 和 `task_consumer`。不直接持有 driver、history 或 tool_registry。

## Factory 设计

factory 位于 `driver/factory.py`。

它做两件事：

- `normalize_driver_config(agent_cfg)`：把配置文件里的 agent 配置归一化成 `AgentDriverConfig`
- `build_agent_driver(host, driver_config)`：根据 `driver_type` 创建具体 driver 实例

## 现有 driver 实现

### Native Driver

文件：`driver/nativeDriver.py`

主要逻辑：

- 使用 host 的 `tool_registry` 获取可用工具
- 调用 `host._infer(tools)` 执行模型推理
- 收到 tool_calls 后返回给 TurnRunner 的 `_dispatch_tool_calls()` 处理
- 检查本轮是否通过 `send_chat_msg` 或 `finish_chat_turn` 完成
- 如果没完成，注入 reminder 后重试

适合场景：

- 模型接口是 OpenAI-compatible chat completion
- 工具调用由当前系统 `funcToolService` 统一提供

### Claude SDK Driver

文件：`driver/claudeSdkDriver.py`

主要逻辑：

- 在 `startup()` 中建立持久 Claude SDK 会话
- 通过 MCP tool 暴露 `send_chat_msg` 和 `finish_chat_turn`
- 每轮把房间增量消息拼成 prompt 发给 SDK
- 直接调用 `host._execute_tool()` 执行工具
- 监听 SDK 流式消息
- 当工具返回表明"本轮结束"后主动 interrupt

适合场景：

- 需要长期会话状态
- 需要 SDK 自身的 tool / thinking / 多段消息能力

## 统一动作设计

不管是哪种 driver，系统认可的核心动作目前只有两种：

- `send_chat_msg`
- `finish_chat_turn`

它们最终都由 `funcToolService/tools.py` 处理并落到统一后端语义：

- `send_chat_msg` → `ChatRoom.add_message(...)`
- `finish_chat_turn` → `ChatRoom.finish_turn(...)`

## 配置建议

### 推荐配置

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

## 当前限制

- `native` driver 仍然沿用"模型推理 + 工具循环"的现状执行方式，尚未抽象为更高层动作协议
- "动作协议"还没有被单独抽成通用抽象

## 推荐的后续演进

1. 把 agent 配置逐步切到 `driver.type`
2. 抽出统一的动作协议层
3. 新增 `gemini_cli` driver 并补完整测试
