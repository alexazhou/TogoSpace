# TurnRunner 成为 AgentDriverHost — 技术方案

> **状态：已完成。** 本方案已全部实施。TurnRunner 实现 AgentDriverHost 协议，自建 driver/tool_registry/history，由 Consumer 内部创建。Agent 不再暴露 turn_runner。

## 背景

当前 `AgentTurnRunner` 持有 `self._agent: Agent` 引用，从中读取 6 项数据（gt_agent、driver、_history、tool_registry、system_prompt、current_db_task）。这导致 TurnRunner 与 Agent 存在双向耦合：

```
Agent 创建 TurnRunner（传 self）
TurnRunner 持有 Agent，反向访问 Agent 的全部字段
```

目标：**让 TurnRunner 直接持有所需字段并实现 AgentDriverHost 协议**，使 Agent 不再是 driver 的宿主，TurnRunner 也不再持有 Agent 引用。

## 当前 AgentDriverHost 协议

```python
class AgentDriverHost(Protocol):
    gt_agent: GtAgent
    system_prompt: str
    agent_workdir: str
    _history: AgentHistoryStore
    tool_registry: AgentToolRegistry

    async def _execute_tool(self) -> None: ...
```

Driver 通过 `self.host` 访问以上字段和方法。当前 host 是 TurnRunner 实例。

## 各 Driver 对 host 的依赖

| 访问项 | nativeDriver | tspDriver | claudeSdkDriver |
|--------|:---:|:---:|:---:|
| `host.gt_agent` | — | ✓ | ✓ |
| `host.system_prompt` | — | — | ✓ |
| `host.agent_workdir` | — | ✓ | — |
| `host._history` | — | — | ✓ |
| `host.tool_registry` | ✓ | ✓ | ✓ |
| `host._execute_tool()` | — | — | ✓ |

关键发现：
- `host._execute_tool()` 仅 claudeSdkDriver 调用

## 修改方案

### Step 1: TurnRunner 自建所有内部组件

TurnRunner 接收纯值参数，自己构造 driver、tool_registry、history：

```python
class AgentTurnRunner:
    def __init__(
        self, *,
        gt_agent: GtAgent,
        system_prompt: str,
        agent_workdir: str = "",
        driver_config: AgentDriverConfig,
    ):
        self.gt_agent = gt_agent
        self.system_prompt = system_prompt
        self.agent_workdir = agent_workdir
        self._history = AgentHistoryStore(gt_agent.id or 0)
        self.tool_registry = AgentToolRegistry()
        self.driver = build_agent_driver(self, driver_config)
```

Agent 不再持有 driver / tool_registry / history 字段，全部通过 `self.turn_runner.xxx` 访问。

Agent 的 `startup()` / `close()` 改为：
```python
async def startup(self):
    await self.turn_runner.driver.startup()

async def close(self):
    self.stop_consumer_task()
    await self.turn_runner.driver.shutdown()
    self.turn_runner.tool_registry.clear()
```

### Step 2: TurnRunner 实现 AgentDriverHost 协议

TurnRunner 已实现：
- `_execute_tool()` 方法（已有）

### Step 3: 清理 Agent

Agent 变为纯 facade，移除所有 DriverHost 相关代码：
- 删除 `driver` 字段 → 通过 `self.turn_runner.driver` 访问
- 删除 `_tool_registry` 字段 → 通过 `self.turn_runner.tool_registry` 访问
- 删除 `_history_store` 字段 → 通过 `self.turn_runner._history` 访问
- 删除 `_infer()` 方法
- 删除 `_execute_tool()` 方法
- 删除 `_history` property、`tool_registry` property → 改为透传 turn_runner
- `current_db_task` property（只读）→ 保留，透传 `task_consumer.current_db_task`

Agent 构造函数简化为：
```python
def __init__(self, gt_agent, system_prompt, driver_config=None, agent_workdir=""):
    self.gt_agent = gt_agent
    self.turn_runner = AgentTurnRunner(
        gt_agent=gt_agent,
        system_prompt=system_prompt,
        agent_workdir=agent_workdir,
        driver_config=driver_config or AgentDriverConfig(),
    )
    self.task_consumer = AgentTaskConsumer(
        gt_agent=gt_agent,
        turn_runner=self.turn_runner,
    )
```

### Step 4: 更新 AgentDriverHost Protocol

```python
class AgentDriverHost(Protocol):
    gt_agent: GtAgent
    system_prompt: str
    agent_workdir: str
    _history: AgentHistoryStore
    tool_registry: AgentToolRegistry

    async def _execute_tool(self) -> None: ...
```

### Step 5: 更新测试

- TurnRunner 构造方式从 `AgentTurnRunner(agent)` 改为关键字参数
- Driver 的 host 从 Agent 变为 TurnRunner（影响 driver 测试中的 mock host）
- Agent 不再有 `_infer` / `_execute_tool` 方法

## 修改影响范围

| 文件 | 变更内容 |
|------|---------|
| `agentTurnRunner.py` | 重构构造函数，所有 `self._agent.xxx` 改为 `self.xxx`，自行构造 driver，实现 DriverHost 协议 |
| `agent.py` | 删除 driver 字段，删除 DriverHost 转发方法，turn_runner 先于 consumer 构造 |
| `driver/base.py` | Protocol 移除 `current_db_task` |
| `driver/claudeSdkDriver.py` | `host._execute_tool()` 保持不变（TurnRunner 实现） |
| `driver/nativeDriver.py` | 无变化（host 类型不变） |
| `driver/tspDriver.py` | 无变化（host 类型不变） |
| `tests/` | 更新构造和 mock 方式 |

## 最终组件关系

```
Agent (facade — 仅持有 gt_agent + system_prompt + task_consumer)
 └── task_consumer: AgentTaskConsumer (拥有运行时状态，内部创建 TurnRunner)
      ├── gt_agent (GtAgent 模型)
      ├── status, current_db_task, _aio_consumer_task
      ├── start(), stop(), consume(), resume_failed()
      └── _turn_runner: AgentTurnRunner (内部创建，实现 AgentDriverHost)
           ├── gt_agent, system_prompt, agent_workdir  (构造时传入)
           ├── _history: AgentHistoryStore    (自建)
           ├── tool_registry: AgentToolRegistry  (自建)
           ├── driver: AgentDriver            (自建，host=self)
           ├── _execute_tool()                (Driver 回调)
           ├── _execute_infer(), _infer_to_item(), _run_tool()  (内部推理/工具执行)
           └── run_chat_turn(), pull_room_messages_to_history()
```

Agent 不再出现在 TurnRunner 或 Consumer 的依赖中。组件关系：
- Consumer 内部创建并持有 TurnRunner（调用 run_chat_turn）
- Driver → TurnRunner（作为 host 回调 _execute_tool）
- Agent 只负责对外 API facade，通过 task_consumer 代理一切
