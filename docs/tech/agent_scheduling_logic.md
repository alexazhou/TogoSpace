# Agent 调度逻辑说明文档

本文档详细说明 TeamAgent 中 Agent 的轮转调度逻辑，特别是“发言”与“回合结束”解耦后的设计实现。

## 1. 核心概念

*   **ChatRoom (房间)**：调度逻辑的“状态机”。它维护成员列表、当前发言人索引（`_turn_pos`）以及当前回合是否已有产出。
*   **Scheduler (调度器)**：事件的“分发者”。它监听房间发布的回合开始信号，并驱动对应的 Agent 开始工作。
*   **Turn (回合)**：单个 Agent 获得操作权的周期。
*   **Round (轮次)**：房间内所有成员按顺序各完成一个 Turn 称为一个 Round。

## 2. 调度机制：解耦模型

在最新的重构中，我们采用了 **“行动与完成分离”** 的模型。

### 2.1 为什么要解耦？
*   **灵活性**：Agent 可以在一个回合内发送多条消息，或者先进行私聊再进行公聊。
*   **工具调用**：Agent 可以在不发言的情况下仅调用功能工具（如查询天气、读写文件），并在完成后主动交出控制权。

### 2.2 关键状态变量
在 `ChatRoom` 类中：
*   `_turn_pos`: 当前指向的成员索引。
*   `_current_turn_has_content`: 布尔值，标记当前发言人在本回合内是否调用过 `send_chat_msg`。
*   `_round_skipped`: 集合，记录本轮次中哪些 AI Agent 没说话就结束了回合。

## 3. 回合生命周期

### 3.1 触发 (Start)
1.  房间发布 `ROOM_MEMBER_TURN` 事件（包含 `member_name`、`room_id`、`room_key`、`team_name`）。
2.  `Scheduler` 订阅该事件，并将该房间的任务加入对应 Agent 的 `wait_task_queue`。
3.  Agent 消费队列，根据驱动类型（Native 或 SDK）启动推理循环。

### 3.2 行动 (Action)
*   Agent 可以调用 `send_chat_msg` 向房间发消息。
*   调用后，房间仅更新 `_current_turn_has_content = True`，**不移动**指针。
*   Agent 也可以调用其他业务工具。

### 3.3 结束 (Finish)
*   **AI Agent**：必须显式调用 `finish_chat_turn()` 工具。
    *   底层触发 `ChatRoom.finish_turn()`。
    *   如果 `_current_turn_has_content` 为 `False`，该 Agent 被加入 `_round_skipped`。
    *   指针 `_turn_pos` 递增，发布下一个成员的 `ROOM_MEMBER_TURN` 事件。
*   **人类操作者 (Operator)**：
    *   通过 TUI 发送消息时，后端在 `RoomMessagesHandler` 中会自动调用 `finish_turn`，实现“发完即结束”的快捷体验。

## 4. 状态流转与停止

### 4.1 激活与唤醒
*   当房间处于 `IDLE`（空闲）状态时，任何成员（特别是 Operator）发送消息都会重置轮次并唤醒房间，将其设为 `SCHEDULING` 状态。

### 4.2 停止调度（滑动窗口判定）
房间采用“自上次发言以来的连续跳过”模型来判定是否停止调度，以避免不必要的空转。

房间会在以下情况进入 `IDLE` 状态并停止发布新事件：
1.  **全员跳过（滑动窗口）**：当系统发现自上一条真实消息（非系统消息）产生以来，**所有 AI Agent 都已经至少执行过一次跳过动作**（即未发言即调用 `finish_chat_turn`），则立即停止。
    *   *注：这意味着停止可能发生在 Round 的中间，而不需要等到 Round 结束。*
2.  **达到上限**：当 `_turn_index` 达到房间配置的 `max_turns`（仅在 Round 结束时判定）。

只要有任何成员发送了真实消息，`_round_skipped` 记录会被立即清空，重新开始一轮窗口判定。

## 5. 开发者指南：如何影响调度

*   **想让 Agent 连续说多句话？**
    在 Prompt 中告知它在一轮内多次调用 `send_chat_msg`，最后再调用 `finish_chat_turn`。
*   **想让 Agent 跳过本轮？**
    直接调用 `finish_chat_turn` 且不调用 `send_chat_msg`。
*   **想从外部干预进度？**
    调用 `room.finish_turn(agent_name)` 强制推进。

## 6. 关键类与方法索引

*   `src/service/roomService.py`: `ChatRoom._update_turn_state_on_finish`, `ChatRoom.finish_turn`
*   `src/service/schedulerService.py`: `_on_member_turn`
*   `src/service/funcToolService/tools.py`: `send_chat_msg`, `finish_chat_turn`
*   `src/service/agentService/driver/`: 各种 Driver 的 `run_chat_turn` 循环判定。
