# Agent Task Refactor Plan

## 目标

将当前 `service/agentService/agent.py` 中混合的任务调度、任务执行、续跑恢复、turn 运行、tool 编排等职责逐步拆分，降低单文件复杂度，同时保持现有行为稳定。

本次重构遵循两个原则：

1. 先冻结行为，再重构结构
2. 每一步都应可独立验证、可独立提交

## 当前问题

当前 `Agent` 类同时承担了多种职责：

- 任务消费入口
- 任务状态迁移
- 单条任务执行与收尾
- 失败任务恢复
- host-managed turn 循环
- tool 调用编排
- 运行时状态广播

这导致：

- 正常任务执行与失败任务续跑虽然已经共用主流程，但职责边界仍然不清晰
- `agent.py` 文件过长，难以维护
- 后续继续优化"续跑"逻辑时，容易牵连调度层与执行层

## 最终结构（已实施）

重构后拆为三个职责模块（原方案计划 4 个模块，实施中将 Executor 合并入 Consumer 以减少模块数和不必要的间接层）：

### 1. Agent (`agent.py`, ~137 行)

协调器角色：运行时壳子与外部门面。

负责：

- 运行时属性：`status`, `consumer_task`, `current_db_task`
- 组件装配：`driver`, `turn_runner`, `task_consumer`
- 生命周期：`startup()` / `close()`
- 对外入口：`start_consumer_task()` / `stop_consumer_task()` / `resume_failed()` / `consume_task()` / `run_chat_turn()`
- AgentDriverHost 协议：`_infer()` / `_execute_tool()` / `pull_room_messages_to_history()`
- 状态广播：`_publish_status()`

### 2. AgentTaskConsumer (`agentTaskConsumer.py`, ~156 行)

任务管道：认领 → 执行 → 状态流转。合并了原 AgentTaskExecutor 的职责。

负责：

- 消费 pending task（循环取任务）
- 接收 resumed running task
- 调用任务状态迁移（`PENDING -> RUNNING`, `FAILED -> RUNNING`）
- 执行单条 task（原 `_execute_claimed_task`）
  - 成功标 `COMPLETED`
  - 失败标 `FAILED`
  - 维护 `current_db_task` 与运行时状态
- 直接调用 `turn_runner.run_chat_turn()` 执行 turn

### 3. AgentTurnRunner (`agentTurnRunner.py`, ~297 行)

Turn 级执行引擎。8 个类方法，所有 driver 共享统一的工具执行路径（通过 tool_registry）。

| 方法 | 可见性 | 说明 |
|------|--------|------|
| `run_chat_turn` | 公开 | Turn 入口：同步消息 → 推理 → 工具循环 |
| `pull_room_messages_to_history` | 公开 | 拉取房间未读消息写入 history |
| `_run_chat_turn_with_host_loop` | 内部 | Host-managed turn loop：推理+工具+重试 |
| `_resume_chat_turn_with_host_loop` | 内部 | 从断点续跑 host loop |
| `_run_until_reply` | 内部 | 单轮推理-工具循环（达到 max 次或 turn 完成） |
| `_infer` | 内部 | 单次 LLM 推理，支持续跑（`resume_item`） |
| `_execute_tool` | 内部 | DriverHost 协议：执行最后 assistant 消息的 tool calls，委托 `_dispatch_tool_calls` |
| `_dispatch_tool_calls` | 内部 | 批量分发 tool calls（通过 tool_registry），支持续跑 |
| `_execute_and_record_tool_call` | 内部 | 底层：执行单个 tool call + 记录 history |

### 4. AgentHistoryStore (`agentHistoryStore.py`, ~197 行)

Agent 历史消息存储与查询。

负责：

- 内存中管理 history items（`_items` 列表）
- 持久化读写（append / finalize）
- 查询方法：`get_last_assistant_message`, `find_tool_call_by_id`, `find_tool_result_by_call_id`
- Turn 状态查询：`get_unfinished_turn_start_index`, `has_unfinished_turn`
- 消息导出：`export_openai_message_list`

## 调用关系

```text
外部调用者（Controller / Scheduler / 测试）
  │
  ├── Agent.start_consumer_task()     → AgentTaskConsumer.consume()
  ├── Agent.resume_failed()           → AgentTaskConsumer.resume_failed()
  ├── Agent.consume_task()            → AgentTaskConsumer.consume()
  ├── Agent.run_chat_turn()           → AgentTurnRunner.run_chat_turn()
  │
  └── AgentDriverHost 协议回调
      ├── Agent._infer()              → AgentTurnRunner._infer()
      ├── Agent._execute_tool()       → AgentTurnRunner._execute_tool()
      └── Agent.pull_room_messages_to_history() → AgentTurnRunner.pull_room_messages_to_history()

AgentTaskConsumer 内部：
  consume() → _execute_task() → turn_runner.run_chat_turn()

AgentTurnRunner ←→ AgentHistoryStore：
  TurnRunner 通过 agent._history 读写历史，HistoryStore 提供查询
  （find_tool_call_by_id, get_unfinished_turn_start_index, etc.）
```

## 分步实施记录

### Step 0: 冻结行为

依赖现有测试冻结当前行为。核心回归测试 53 个用例全部通过，全量 289 个用例通过。

### Step 1: 先在 agent.py 内部分区

将方法按职责分为四个 Zone（生命周期、任务入口、任务执行、Turn 运行层），不拆文件。

### Step 2: 抽出 AgentTurnRunner

将 14 个 Turn 层方法迁移至 `agentTurnRunner.py`，Agent 保留薄委托。

### Step 3: 抽出 AgentTaskExecutor

将 `_execute_claimed_task` 迁移至 `agentTaskExecutor.py`。

### Step 4: 抽出 AgentTaskConsumer

将消费循环和 `resume_failed` 逻辑迁移至 `agentTaskConsumer.py`。

### Step 5: 收敛与优化

- 合并 AgentTaskExecutor 入 AgentTaskConsumer（Executor 仅 1 个方法，独立模块内聚度不足）
- Consumer 直接调用 `turn_runner.run_chat_turn()`，不再绕道 Agent 委托
- 删除 Agent 上不再需要的内部委托（`_execute_claimed_task`）
- 移除 TurnRunner 上的 6 个便捷属性，改为 `self._agent.xxx` 直接访问
- 更新 Agent docstring 与注释，明确协调器角色

### Step 6: TurnRunner 方法精简与职责归位

- 合并 `_infer` + `_resume_infer_history_item` → 单方法 `_infer(tools, *, resume_item=None)`
- 合并 `_execute_tool_call_with_history` + `_execute_tool_call_with_existing_history` → 单方法（`existing_item=None`）
- 移入 AgentHistoryStore：`find_tool_call_by_id`（原 `_find_tool_call_in_history`）
- 移入 AgentHistoryStore：`get_unfinished_turn_start_index` + `has_unfinished_turn`
- 为所有 TurnRunner 方法补充 docstring
- 方法重命名以明确语义：`_dispatch_tool_calls`, `_execute_and_record_tool_call`
- 统一工具执行路径：Claude SDK driver 注册工具到 `tool_registry`，`_execute_tool` 委托 `_dispatch_tool_calls`，删除 `_invoke_func_tool` 模块级函数
- 更新本文档反映最终结构与方法清单

## 每步实施要求

每完成一步，都应执行最少回归：

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
