# 任务驱动型 Agent 自动唤醒功能设计文档

## 1. 背景与目标
目前 Agent 的行动主要由聊天室消息 (`ROOM_MESSAGE`) 驱动。为了增强 Agent 的自主性并确保协作任务 (`GtAgentTask`) 能够被及时处理，我们需要引入一种新的驱动机制：**任务驱动型唤醒**。

目标是将 Agent 的工作模式统一为：**收到通知 (消息/任务) -> 执行行动 -> 结束行动 (finish_action)**。

## 2. 核心概念
- **统一驱动源**：通过 `GtScheculeTask` 统一管理所有唤醒原因。
- **优先级调度**：聊天消息 (`ROOM_MESSAGE`) 优先级高于协作任务 (`COLLABORATION_TASK`)。
- **纯净任务上下文**：处理任务时，不注入房间闲聊历史，确保 Agent 专注。

## 3. 技术实现方案

### 3.1 数据模型扩展
在 `src/constants.py` 的 `AgentTaskType` 枚举中新增类型：
- `ROOM_MESSAGE`: 聊天驱动（现有）
- `COLLABORATION_TASK`: 协作任务驱动（新增）

### 3.2 优先级调度逻辑
修改 `gtScheculeTaskManager.get_first_unfinish_task` 的查询逻辑：
- 排序规则：`task_type ASC` (ROOM_MESSAGE=1, COLLABORATION_TASK=2), `id ASC`。
- 效果：只要有新消息待处理，Agent 优先回消息；只有消息处理完后，才会开始处理 TODO 任务。

### 3.3 自动排程机制 (Consumer 逻辑)
在 `AgentTaskConsumer.consume()` 循环中，当 Agent 执行完一次完整的行动（即调用了 `finish_action` 且当前调度记录已标记为 `COMPLETED`）后，增加“任务扫描”钩子：
1. **触发时机**：Agent 调用 `finish_action` 结束本轮行动之后。
2. **逻辑**：
   - 检查 `agent_tasks` 表，获取该 Agent 最早的一个 `status` 为 `TODO` 或 `IN_PROGRESS` 的任务。
   - 检查 `schecule_tasks` 表，确保该任务没有对应的 PENDING 记录（幂等）。
   - 如果满足条件，自动创建一条 `COLLABORATION_TASK` 类型的调度记录。

### 3.4 纯净上下文注入 (Runner 逻辑)
在 `AgentTurnRunner.run_chat_turn` 中，根据任务类型分流：
- **ROOM_MESSAGE**：维持现有“同步消息 -> 注入历史”逻辑。
- **COLLABORATION_TASK**：
  1. 跳过房间消息同步。
  2. 获取协作任务详情。
  3. 注入专属系统提示：
     ```text
     【任务通知】
     你当前被唤醒以处理以下任务：
     - 标题: {title}
     - 描述: {description}
     - 状态: {status} (TODO 或 IN_PROGRESS)
     
     请直接开始工作。
     - 若完成，请调用 `update_task` 将状态改为 DONE 并填写结果。
     - 若需取消，请调用 `update_task` 将状态改为 CANCELLED。
     - 无论成败，完成后必须调用 `finish_action`。
     ```

## 4. 关键流程示例
1. Agent A 正在回复房间消息。
2. Agent A 调用 `finish_action` 结束本轮。
3. `AgentTaskConsumer` 发现消息任务已完成，扫描发现 A 还有一个 `TODO` 的“整理文档”任务。
4. `AgentTaskConsumer` 自动生成一个 `COLLABORATION_TASK` 并立即在下一轮循环中认领。
5. Agent A 再次被唤醒，此时上下文只有“整理文档”的任务说明。
6. Agent A 整理完文档，调用 `update_task(status="DONE")`，然后调用 `finish_action`。
7. 调度器再次扫描，若无更多任务，Agent 进入 `IDLE` 状态。

### 3.5 自动重唤醒机制 (预期行为)
本设计包含一个“强制性唤醒”特性：如果 Agent 在处理 `COLLABORATION_TASK` 时，由于某种原因（如 token 限制或任务繁重）没有将任务状态修改为终态就直接调用了 `finish_action`，调度器在下一次扫描时会**再次唤醒**它。
- **目的**：确保 Agent 对任务负责，防止任务在未完成时被意外遗忘。
- **闭环条件**：Agent 必须显式将任务状态改为非 `TODO` 且非 `IN_PROGRESS` 状态，调度器才会停止针对该任务的自动唤醒。

## 4. 关键流程示例
...

## 5. 待讨论细节
1. **多任务顺序**：如果 Agent 有多个 TODO 任务，目前按 ID 升序（即最早的任务优先）处理。是否需要引入优先级权重排序？
