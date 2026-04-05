# Agent Task Consumer Plan A

## 背景

当前 `Agent` 已经开始承担协调器角色，但任务运行时状态仍散落在 `Agent` 与 `AgentTaskConsumer` 之间，导致职责边界仍不够清晰。

目前已经明确的目标是：

- 调用链保持为：`Agent -> AgentTaskConsumer -> AgentTurnRunner`
- 不引入额外的 host interface
- 不让 `AgentTaskConsumer` 通过“完整 Agent 反向控制 Agent”形成强耦合

在这个前提下，采用 **方案 A：task runtime 轻量托管给 Consumer**。

## 方案 A 核心思路

将“任务运行时状态”统一托管给 `AgentTaskConsumer`，但暂时不迁移 `TurnRunner` / `driver` 所依赖的核心宿主能力。

也就是说：

### 托管给 AgentTaskConsumer 的内容

- `status`
- `_aio_consumer_task`
- `current_db_task`
- 状态广播 `_publish_status()`
- 任务消费循环
- 任务恢复入口
- 任务执行收尾

### 继续保留在 Agent 的内容

- `gt_agent`
- `system_prompt`
- `driver`
- `_history`
- `_tool_registry`
- `turn_runner`
- Driver Host 所需能力：`_infer()` / `_execute_tool()`

### Agent 的角色

Agent 最终只保留：

- 生命周期管理
- 组件装配
- 轻量封装接口
- 对 `task_consumer` 的委托

## 为什么选方案 A

相较于让 `TaskConsumer` 直接依赖完整 `Agent`，方案 A 的好处是：

1. 任务 runtime 职责更集中
2. `Agent` 更接近 facade
3. 不需要先改 `driver` host 模型
4. 不需要立刻把 `TurnRunner` 与 `driver` 的依赖关系重写
5. 风险明显低于“彻底迁移所有运行时字段”

## 设计边界

### AgentTaskConsumer 负责

- 管理任务消费协程
- 管理 `status`
- 管理 `current_db_task`
- 从 DB 取 task
- 执行 task 的状态流转
- 调用 `turn_runner.run_chat_turn(...)`
- 完成 / 失败收尾
- 任务失败恢复

### AgentTaskConsumer 不持有 Agent

本方案明确约束：

- `AgentTaskConsumer` 不直接持有 `Agent` 对象
- 不通过 `agent.xxx` 方式反向调用 `Agent`

改为由 `Agent` 在初始化时显式注入最小依赖。

推荐注入项：

- `gt_agent`
- `max_function_calls`
- `turn_runner`

如后续仍需显式广播状态，可继续注入最小能力，例如：

- `publish_status`

但不传完整 `Agent`。

### Agent 不负责

- 不再直接持有任务消费协程
- 不再直接持有 `status`
- 不再直接持有 `current_db_task`
- 不再直接广播任务运行时状态

### AgentTurnRunner 不变

第一阶段不改 `AgentTurnRunner` 与 `driver` 之间的关系。

保留：

- `Agent` 作为 driver host
- `Agent._infer()` / `Agent._execute_tool()` 委托到 `turn_runner`
- `turn_runner` 继续通过 `Agent` 访问 `_history`、`tool_registry`、`driver`

## 实施后的结构

### Agent

保留字段：

- `gt_agent`
- `system_prompt`
- `agent_workdir`
- `max_function_calls`
- `_history_store`
- `_tool_registry`
- `driver`
- `turn_runner`
- `task_consumer`

改造成 property 代理的字段：

- `status`
- `current_db_task`
- `is_active`

保留方法：

- `startup()`
- `close()`
- `start_consumer_task()`
- `stop_consumer_task()`
- `resume_failed()`
- `_infer()`
- `_execute_tool()`

### AgentTaskConsumer

新增/托管字段：

- `gt_agent: GtAgent`
- `status: AgentStatus`
- `_aio_consumer_task: asyncio.Task | None`
- `current_db_task: GtAgentTask | None`
- `_turn_runner: AgentTurnRunner`

保留/扩展方法：

- `start(initial_task=None)`
- `stop()`
- `consume(initial_task=None)`
- `resume_failed()`
- `_publish_status(status)`
- `_execute_task(...)`

## 分步实施方案

### Step 1: 将 runtime 字段迁入 AgentTaskConsumer

从 `Agent` 迁出：

- `status`
- `_aio_consumer_task`
- `current_db_task`

迁入 `AgentTaskConsumer`：

- `self.status`
- `self._aio_consumer_task`
- `self.current_db_task`

同时在 `Agent` 中增加 property 代理：

- `status`
- `current_db_task`

`is_active` 改为读取 `task_consumer` 状态。

#### 目标

先只做字段托管，不改调用入口语义。

### Step 2: 将状态广播迁入 AgentTaskConsumer

把 `_publish_status()` 从 `Agent` 搬到 `AgentTaskConsumer`。

此后：

- `status` 的修改与广播都由 consumer 自己负责
- `Agent` 不再直接广播任务运行态

### Step 3: 将 consumer 协程生命周期迁入 AgentTaskConsumer

把这些逻辑迁入 `AgentTaskConsumer`：

- `start_consumer_task()` 的实际启动逻辑
- `stop_consumer_task()` 的实际停止逻辑

`Agent` 保留同名方法，但只做一层转发：

- `Agent.start_consumer_task(...) -> self.task_consumer.start(...)`
- `Agent.stop_consumer_task() -> self.task_consumer.stop()`

### Step 4: 将 resume_failed 的内部实现完全收口到 Consumer

`Agent.resume_failed()` 只保留：

```python
await self.task_consumer.resume_failed()
```

所有：

- failed task 查找
- FAILED -> RUNNING
- ACTIVE 状态设置
- 重启消费

都留在 consumer 中。

### Step 5: 清理 Agent 中多余 runtime 逻辑

完成前几步后，`Agent` 中移除：

- runtime 状态字段定义
- consumer task 字段定义
- 直接状态广播逻辑

保留 facade 与 driver host 能力。

## 建议的最终接口形态

### Agent

```python
class Agent:
    @property
    def status(self) -> AgentStatus: ...

    @property
    def current_db_task(self) -> GtAgentTask | None: ...

    @property
    def is_active(self) -> bool: ...

    def start_consumer_task(self, initial_task: GtAgentTask | None = None) -> None: ...
    def stop_consumer_task(self) -> None: ...
    async def resume_failed(self) -> None: ...
```

### AgentTaskConsumer

```python
class AgentTaskConsumer:
    gt_agent: GtAgent
    status: AgentStatus
    current_db_task: GtAgentTask | None

    def __init__(
        self,
        *,
        gt_agent: GtAgent,
        turn_runner: AgentTurnRunner,
        max_function_calls: int,
    ) -> None: ...

    def start(self, initial_task: GtAgentTask | None = None) -> None: ...
    def stop(self) -> None: ...
    async def consume(self, initial_task: GtAgentTask | None = None) -> None: ...
    async def resume_failed(self) -> None: ...
    def _publish_status(self, status: AgentStatus) -> None: ...
```

## TurnRunner 注入方式

由于 `AgentTaskConsumer` 不持有 `Agent`，它调用 turn 逻辑的方式不是：

```python
agent.turn_runner.run_chat_turn(...)
```

而是由 `Agent` 在构造阶段直接把 `turn_runner` 注入给 consumer：

```python
self.turn_runner = AgentTurnRunner(self)
self.task_consumer = AgentTaskConsumer(
    gt_agent=self.gt_agent,
    turn_runner=self.turn_runner,
    max_function_calls=self.max_function_calls,
)
```

随后 `AgentTaskConsumer` 在内部直接调用：

```python
await self._turn_runner.run_chat_turn(claimed_task, resumed=resumed)
```

这样可以同时满足：

- 调用链仍是 `Agent -> TaskConsumer -> TurnRunner`
- `TaskConsumer` 不持有完整 `Agent`
- 不需要额外引入 host interface

## 验证要求

每一步都应至少执行以下回归测试：

```bash
PYTHONPATH=src .venv/bin/python -m pytest -o addopts='' \
  tests/integration/test_dal_manager/test.py \
  tests/integration/test_agent_service/test_agent_service.py \
  tests/integration/test_scheduler_service/test.py -q
```

如涉及 controller 行为，再补：

```bash
PYTHONPATH=src .venv/bin/python -m pytest -o addopts='' \
  tests/api/test_agent_controller/test.py -q
```

## 暂不处理的事项

本方案阶段中，以下内容暂不修改：

- `driver` host 模型
- `AgentTurnRunner` 与 `Agent` 的依赖关系
- `_history` / `tool_registry` 的归属
- 引入额外接口层或 protocol

这些问题留到 `Agent` facade 进一步稳定之后再处理。
