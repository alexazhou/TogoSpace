# V17: 人工干预与 Agent 停止能力 - 技术文档

## 1. 方案概览

V17 实现"人工停止 Agent 当前 turn"的能力。核心挑战有两个：

1. **安全中断**：正在执行的 asyncio 协程需要被中断，且中断后状态要干净（task 标记 CANCELLED、agent 回 IDLE）。
2. **上下文感知**：当前 turn 的 history 可能处于残缺状态（有未完成的 INIT 占位），需要在取消时清理并写入中断说明，保证下次调度时 LLM 不基于残缺上下文推理。

---

## 2. 取消机制设计

### 2.1 现有 stop() 的行为

`AgentTaskConsumer.stop()`（经由 `Agent.stop_consumer_task()`）目前在以下三个场景中调用：

| 调用方 | 触发时机 |
|--------|---------|
| `agent.close()` | Agent 实例销毁时（hot reload 前 / 服务关闭时） |
| `schedulerService.shutdown()` | 整个调度器停止（服务关闭） |
| `schedulerService.stop_scheduler_team(team_id)` | 指定 Team 被停止（hot reload 前） |

`stop()` 内部调用 `asyncio.Task.cancel()`，这会向 `consume()` 协程注入 `CancelledError`。`CancelledError` 继承自 `BaseException` 而非 `Exception`，因此 `consume()` 里的 `except Exception` 不会捕获它，它会一路向上穿透，跳过末尾的 cleanup 代码，协程直接退出。

结果是：**task 状态停留在 RUNNING，agent 状态停留在 ACTIVE，cleanup 代码一行未执行**。

这是有意为之。上述三个场景的共同特征是"整个运行时即将被重建"——hot reload 之后新的 `consume()` 会重新启动，它扫到 RUNNING 状态的 task 会直接续跑；服务关闭时运行时不再存在，残留状态无人关心。`stop()` 的语义是**暂停等待续跑**，不是取消。

### 2.2 本次打算采用的取消方案

V17 的"人工停止"语义不同：**彻底放弃当前 turn，agent 回 IDLE，可继续接受新任务**。这要求 task 状态写 CANCELLED、history 清理干净，续跑路径不能被触发。现有 `stop()` 的"直接穿透"行为满足不了这个要求。

但底层机制可以复用。`consume()` 是一个 asyncio 协程，无法从外部直接打断——代码执行到哪里由 `await` 点决定，不能像线程那样强行终止。中断它的唯一方式仍然是 `task.cancel()`：

调用 `task.cancel()` 时，asyncio 向协程注入 `CancelledError`，注入时机取决于协程当前状态：

- **协程正挂起在 `await`（最常见）**：如等待 HTTP 响应、等待 DB 写入，asyncio 立刻将 `CancelledError` 抛入该挂起点，I/O 操作即刻中止，不需等结果返回。
- **协程正在执行 CPU 代码**：只能等协程主动让出控制权（跑到下一个 `await`）时再注入。

推理阶段绝大部分时间挂在 aiohttp 的 I/O 等待上，属于第一种情况，**停止几乎立刻生效**，不需等 LLM 返回完整输出。

与 `stop()` 的区别只有一点：**V17 在 `consume()` 中主动捕获这个 `CancelledError`，在捕获后执行收尾逻辑，而不是让它穿透**。`CancelledError` 被捕获后若不 re-raise，协程不会终止，后续 `await` 仍可正常使用，可以继续执行写 DB、清理 history 等操作，完成后正常退出。

但 `task.cancel()` 产生的 `CancelledError` 并不只来自人工停止——hot reload、服务关闭同样走这条路。为了区分"这次是人工停止"还是"系统关闭"，在 `AgentTaskConsumer` 中新增 `_cancel_requested` 标志：人工停止时先置为 `True` 再调用 `task.cancel()`，捕获到 `CancelledError` 时检查该标志，为 `True` 则走收尾逻辑，否则 re-raise，原有行为不受影响。

### 2.3 具体改造

在 `AgentTaskConsumer` 中新增 `_cancel_requested` 布尔标志和 `cancel_current_turn()` 方法。`cancel_current_turn()` 的逻辑是：先将 `_cancel_requested` 置为 `True`，再调用 `task.cancel()`。

`cancel_current_turn()` 入口做防御性检查：若当前状态不是 `ACTIVE`（agent 已自然完成或处于其他状态），直接返回 False，不做任何操作。

在 `consume()` 的 `run_chat_turn` 调用处新增 `except asyncio.CancelledError` 分支，捕获后检查 `_cancel_requested`：
- 若为 `True`：执行收尾逻辑（history 清理、task 状态更新为 CANCELLED、写活动记录），然后 `break` 进入正常 cleanup，`_set_status(IDLE)` 会被调用，agent 回 IDLE。
- 若为 `False`：re-raise，原有穿透行为不变（hot reload / 服务关闭场景不受影响）。

### 2.4 `_cancel_requested` 标志重置

`_cancel_requested` 标志在两处重置为 `False`，形成双保险：

1. **CancelledError 处理完成后立即重置**（主路径）：在 `consume()` 的 `except CancelledError` 分支中，收尾逻辑执行完毕后重置。
2. **`consume()` 入口处防御性重置**（兜底）：应对"flag 已设但 CancelledError 未注入"的边缘情况——例如 `cancel_current_turn()` 调用瞬间 task 恰好自然完成，CancelledError 未被抛出。下次 `consume()` 启动时清 flag，避免后续 hot reload / 服务关闭场景的 CancelledError 被错误当作人工停止。

---

## 3. History 清理与上下文注入

### 3.1 问题：残缺的 active turn

turn 被中断时，history 末尾可能存在状态为 INIT 的未完成记录（推理占位或工具占位）。若直接删除这些记录，会引发两个问题：

**问题一：工具调用链残缺**
若中断发生在工具执行阶段，TOOL (INIT) 被删除后，其前驱 ASSISTANT (SUCCESS) 的 tool_calls 声明仍在却没有对应结果，LLM 看到的是一个声明了工具调用但没有执行结果的残缺上下文，行为不可预期。

**问题二：模型失忆**
删除记录会丢失当前 turn 内已完成的部分推理过程，LLM 无法感知本轮做了什么、到哪里被中断。

### 3.2 清理与注入逻辑

整体处理原则：**不删除任何记录，将未完成的占位填充为 CANCELLED**，确保每个 ASSISTANT 声明的 tool_call 都有对应的 TOOL 结果（OpenAI 协议要求），最后追加 ROOM_TURN_FINISH 关闭 active turn。

`AgentHistoryStatus` 新增 `CANCELLED` 值，用于与执行失败的 `FAILED` 状态区分，方便事后查看 DB 时区分"正常失败"和"被人工取消"。

中断时 history 末尾只会处于两类状态：

---

**类型 A：中断发生在推理阶段**

history 末尾是一条 ASSISTANT (INIT) 占位，可能出现在 turn 的第一次推理，也可能出现在所有工具已执行完毕、LLM 进行后续推理时：

```
# 第一次推理被中断
ASSISTANT (INIT)

# 工具链完成后再次推理时被中断
ASSISTANT (SUCCESS) — tool_calls=[tc1]
TOOL (SUCCESS)      — tc1
ASSISTANT (INIT)    ← 被中断
```

处理：将末尾 ASSISTANT (INIT) 填充为 CANCELLED。无论哪种子情况，前面已存在的工具链均完整，不需要补写。

填充后该记录仍保留在 history 中，但它是通过 `build_placeholder()` 创建的占位项，没有实际消息内容（`has_message` 为 False）。`build_infer_messages()` 在构建发给 LLM 的消息列表时会过滤掉所有 `has_message` 为 False 的记录，因此这条 CANCELLED 占位不会出现在下次推理的上下文中。

---

**类型 B：中断发生在工具执行阶段**

ASSISTANT 已成功返回并声明了若干 tool_calls，当前正在顺序执行其中某个工具。由于工具是逐一执行的，可能出现三种子情况：

**B-1：当前工具有 INIT，且是 ASSISTANT 声明的最后一个**

```
ASSISTANT (SUCCESS) — tool_calls=[tc1]
TOOL (INIT)         — tc1   ← 正在执行
```

处理：将 TOOL (INIT, tc1) 填充为 CANCELLED。tc1 是唯一的 tool_call，工具链已完整，无需补写。

**B-2：当前工具有 INIT，但 ASSISTANT 还声明了更多后续 tool_calls**

```
ASSISTANT (SUCCESS) — tool_calls=[tc1, tc2]
TOOL (INIT)         — tc1   ← 正在执行，tc2 尚无任何记录
```

处理：将 TOOL (INIT, tc1) 填充为 CANCELLED；tc2 从未创建 INIT，补写一条 TOOL (CANCELLED, tc2)，原因"cancelled by user"。

**B-3：上一个工具刚完成，下一个工具的 INIT 尚未创建**

tc1 已成功返回，tc2 的 INIT 写入之前发生中断（代码执行到两次工具之间的 CPU 段）：

```
ASSISTANT (SUCCESS) — tool_calls=[tc1, tc2]
TOOL (SUCCESS)      — tc1   ← 已完成，无 INIT 占位
```

处理：此时无 INIT 可填充。遍历 ASSISTANT 的 tool_calls 发现 tc2 没有 TOOL 记录，补写一条 TOOL (CANCELLED, tc2)。

---

**收尾步骤（所有情况通用）**

上述处理完毕后，追加一条带 `ROOM_TURN_FINISH` 标签的 USER 消息，注明本轮已被操作者中断，关闭 active turn：

```
... [正常历史] ...
USER:      <ROOM_TURN_BEGIN> 本轮任务内容
ASSISTANT: (SUCCESS) 推理结果，tool_calls=[tc1, tc2]
TOOL:      (CANCELLED) tc1 — cancelled by user   ← 原 INIT，填充为 CANCELLED（B-2 示例）
TOOL:      (CANCELLED) tc2 — cancelled by user   ← 从未执行，补写 CANCELLED 结果
USER:      <ROOM_TURN_FINISH> 本轮任务已被操作者中断，请以下一条新消息为起点重新出发。
```

处理后效果：
- `has_active_turn()` 返回 False（ROOM_TURN_FINISH 先被扫到）
- 下次调度进入正常 turn 路径
- LLM 能看到本轮执行轨迹及中断原因，不会基于残缺上下文盲目续跑

### 3.3 房间消息的处理说明

`ROOM_TURN_BEGIN` 消息是以 SUCCESS 状态写入的，不会被清理，其中包含的任务内容保留在 history 中，LLM 下次可以感知到。

但 `pull_room_messages_to_history()` 在写入时已将这批房间消息标记为"已读"。取消后 agent 回 IDLE，房间不会为这批消息重新触发调度——只有新消息进入才会驱动下一轮。对于私聊，操作者中断后需要重新发消息才能继续；对于群聊，agent 等待房间下一条新消息自然触发。这是预期行为，符合 V17 的产品边界（单轮取消，不自动重试）。

---

## 4. Driver 适用范围

V17 仅支持 `host_managed_turn_loop=True` 的 driver（NativeDriver、TspDriver）。ClaudeSdkDriver（`host_managed_turn_loop=False`，driver 自行管理 turn 循环）的取消行为需单独设计，不在本版本范围内。

CancelledError 在 host-managed 模式下的传播路径清晰：`consume()` → `run_chat_turn()` → `_run_turn_loop()` → `_advance_step()`，中断点通常在 LLM 推理的 I/O 等待处。NativeDriver 无持久状态，无需额外清理；TspDriver 的子进程通信为请求-响应模式，被中断的请求不影响后续新请求。

为未来扩展，在 `AgentDriver` 基类中预留 `cancel_turn()` 方法（默认空实现），供子类覆写以执行 driver 特有的清理逻辑：

```python
class AgentDriver:
    async def cancel_turn(self) -> None:
        """人工取消当前 turn 时调用。子类可覆写以执行 driver 特有的清理。"""
        pass
```

`handle_cancel_turn()` 的调用链路：`AgentTaskConsumer` 捕获 CancelledError → 调 `AgentTurnRunner.handle_cancel_turn()` → 先调 `self.driver.cancel_turn()` → 再调 `self._history.finalize_cancel_turn()`。

---

## 5. 后端改动范围

| 文件 | 变更内容 |
|------|---------|
| `src/service/agentService/driver/base.py` | `AgentDriver` 新增 `cancel_turn()` 方法（默认空实现） |
| `src/service/agentService/agentHistoryStore.py` | 新增 `finalize_cancel_turn()` — 将当前 turn 内所有 INIT 占位更新为 CANCELLED，并为未执行的 tool_call 补写 CANCELLED 结果 |
| `src/service/agentService/agentTurnRunner.py` | 新增 `handle_cancel_turn()` |
| `src/service/agentService/agentTaskConsumer.py` | 新增 `_cancel_requested` 字段 + `cancel_current_turn()` + `CancelledError` 处理分支 + 标志重置逻辑 |
| `src/service/agentService/agent.py` | 新增 `cancel_current_turn()` facade |
| `src/controller/agentController.py` | 新增 `AgentStopHandler`（`POST /agents/<id>/stop.json`） |
| `src/route.py` | 注册路由 |

### AgentStopHandler 接口

```
POST /agents/<agent_id>/stop.json
```

前置校验：
- agent 在运行时中存在（`agentService.get_agent(agent_id)`）
- agent 当前状态为 `ACTIVE`（否则返回 `agent_not_active` 错误）

响应：
```json
{"status": "stopped", "agent_id": 123}
```

---

## 6. 活动记录补充

现有 `_set_status()` 在写活动记录时，`status` 固定为 `AgentActivityStatus.SUCCEEDED`。取消场景下，agent 回 IDLE 的活动记录应使用 `AgentActivityStatus.CANCELLED`，以便在活动列表中区分"正常空闲"与"被人工停止后空闲"。

具体做法：在 `_set_status()` 中增加参数或在 CancelledError 处理分支中单独写入一条 `CANCELLED` 状态的活动记录（在调用 `_set_status(IDLE)` 之前），标注"Turn 被操作者停止"。

---

## 7. 前端改动范围

| 文件 | 变更内容 |
|------|---------|
| `frontend/src/api.ts` | 新增 `stopAgent(agentId: number)` |
| `frontend/src/components/AgentActivityDialog.vue` | 新增 `stopping` ref + `handleStop()` + stop 按钮 |

### 停止按钮显示规则

- 仅当 `currentStatus === 'active'` 时可见
- 操作中（`stopping === true`）时禁用并展示 loading 态
- 成功后 toast 提示，等待 WS 推送状态更新

---

## 8. 事件链路

停止操作不引入新的 WS 事件类型，复用现有链路：

1. 前端调用 `POST /agents/<id>/stop.json`
2. 后端 `cancel_current_turn()` → `CancelledError` → cleanup → `_set_status(IDLE)`
3. `_set_status(IDLE)` 发布 `AGENT_STATUS_CHANGED` 消息总线事件
4. WS 推送 `agent_status` 事件至前端
5. 前端 `runtimeStore` 更新 agent 状态，`AgentActivityDialog` 响应式更新

---

## 9. 测试要点

- `cancel_current_turn()` 在 agent ACTIVE 时能正常中断，task 状态变为 CANCELLED
- `cancel_current_turn()` 在 agent 非 ACTIVE 状态时返回 False，不执行任何操作
- 中断后 `has_active_turn()` 返回 False
- 中断后 history 末尾包含 ROOM_TURN_FINISH 标记与中断说明
- 中断后原 INIT 占位项状态变为 CANCELLED，错误原因为"cancelled by user"
- 中断后 agent 状态变为 IDLE，不进入 FAILED
- agent 之后可正常接收新任务并执行
- 对 IDLE / FAILED 状态 agent 调用 stop 接口返回 `agent_not_active` 错误
- 活动记录中出现 `CANCELLED` 状态的活动记录，可与正常执行失败区分
- `_cancel_requested` 标志在处理完成后被正确重置为 False

---

## 10. 结论

V17 通过三个关键设计解决 Agent 停止问题：

1. **`_cancel_requested` 标志** 区分"人工停止"与其他 `CancelledError` 来源，确保清理逻辑只在主动停止时执行，不干扰原有的 hot reload / 服务关闭场景。标志在消费后和 `consume()` 入口处双重重置，避免残留。
2. **`handle_cancel_turn()`** 将 INIT 占位填充为 CANCELLED（保留轨迹、修复工具链）+ 写入带 `ROOM_TURN_FINISH` 的中断说明，保证 history 状态干净且完整，下次调度 LLM 能正确感知上下文。
3. **Driver 层 `cancel_turn()` 预留接口** 为不同 driver 类型的清理逻辑提供扩展点。V17 仅覆盖 host-managed 模式（Native/TSP），ClaudeSdkDriver 的取消支持后续单独实现。
