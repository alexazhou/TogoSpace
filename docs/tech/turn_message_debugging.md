# 轮次与消息排查指南

## 概述

本文档记录多 Agent 多房间对话场景中，**"消息丢失"** 与 **"轮次顺序混乱"** 两类常见疑似 Bug 的排查方法。

> 典型用户反馈：
> - "某个 Agent 在某一轮没有说话"
> - "多个 Agent 的消息顺序似乎不对"
> - "Agent 看到的历史记录里少了几条消息"

这些现象通常 **不是调度 Bug**，而是 LLM 行为选择与多房间交叉的正常结果。下面提供排查路径，帮助快速定位问题根因。

---

## 核心机制速览

### 消息同步流程

```
┌─────────────┐   get_unread_messages   ┌─────────────┐
│  ChatRoom   │ ───────────────────────► │ TurnRunner  │
│  messages[] │   返回新消息 + 推进      │  _history   │
│  read_index │   read_index             │             │
└─────────────┘                          └──────┬──────┘
                                                │
                                    过滤自己发的消息
                                    (sender_id == self)
                                                │
                                         synced_count
                                      (0=无他人消息, 1=有)
```

**关键点：**

- `get_unread_messages` 会 **推进 read_index**（副作用），即使最终过滤后无他人新消息
- 如果未读消息全部是自己发的（跨房间场景），`synced_count == 0` → 自动跳过
- `synced_count` 返回 0 或 1（有/无），不返回具体消息条数

### 轮次状态机

```
INIT ──activate──► SCHEDULING ──max_turns/all_skipped──► IDLE
                       │                                   │
                       │          ◄── new message ─────────┘
                       │
               Agent 轮流行动：
               turn_pos 在 agent_ids[] 中循环
               turn_pos 回到 0 时 turn_count++
```

### Agent 行动选择

每个 Agent 在自己的轮次中，LLM 可能做出以下几种行为：

| LLM 工具调用 | 含义 | has_content |
|---|---|---|
| `send_chat_msg` + `finish_chat_turn` | 发消息并结束行动 | True |
| 仅 `finish_chat_turn` | 选择不发言 | False |
| `send_chat_msg`（多条）+ `finish_chat_turn` | 发多条消息 | True |

**`has_content=False` 不是 Bug** — LLM 认为当前无需发言是正常行为。

### Turn / Step 概念

- `turn`：处理“某个房间轮到当前 Agent 发言”的整轮过程
- `step`：turn 内部的一次推进动作，例如一次推理、一次执行工具、一次恢复待执行工具

排查日志时建议按这个层级理解：

- 看房间是否推进，要看 turn 是否完成（通常由 `finish_chat_turn` 触发）
- 看为什么 turn 卡住，要看某个 step 是否一直落在 `NO_ACTION`

---

## 排查流程

### 第一步：确认现象类型

| 现象 | 最可能的原因 | 排查方向 |
|---|---|---|
| 某 Agent 某轮没说话 | LLM 选择不发言 | 查 has_content |
| 消息顺序混乱 | 多房间消息交叉 | 查 Agent 所属房间 |
| Agent 看不到某条消息 | 消息发送在 read_index 推进之后 | 查消息同步日志 |
| 轮次卡住不动 | 调度或重试异常 | 查 Consumer 日志 |

### 第二步：查日志

所有关键日志都在以下位置：

```
logs/backend/service/roomService.log    — 房间调度与轮次
logs/backend/service/agentService.log   — 消息同步与工具调用
```

#### 2.1 确认 Agent 是否发言

搜索 `结束本轮行动`：

```
房间 team1/general 由 小马哥(agent_id=3) 结束本轮行动 (has_content=True, turn_pos=2/4, turn_count=1)
```

- `has_content=True`：Agent 发送了消息
- `has_content=False`：LLM 选择不发言
- `turn_pos=2/4`：当前是第 3 位（0-based），共 4 个 Agent
- `turn_count=1`：当前是第 2 轮（0-based）

#### 2.2 确认消息同步情况

搜索 `同步房间消息`：

```
同步房间消息: agent=小马哥(agent_id=3), room=general, raw=5, own=2, others=3
```

- `raw=5`：`get_unread_messages` 返回 5 条未读
- `own=2`：其中 2 条是自己发的（被过滤）
- `others=3`：实际追加到历史的他人消息数

**如果 `raw > 0` 但 `others == 0`**：所有未读都是自己跨房间发的消息，属于正常情况。

#### 2.3 确认自动跳过

搜索 `无新消息，自动跳过本轮`：

```
无新消息，自动跳过本轮: 小马哥(agent_id=3), room=general
```

出现这条日志说明 Agent 的 `synced_count == 0`（没有他人新消息），系统自动结束其轮次，没有调用 LLM。

#### 2.4 确认 LLM 调用了哪些工具

搜索 `检测到工具调用`：

```
检测到工具调用: 小马哥(agent_id=3), tools=['send_chat_msg', 'finish_chat_turn']
```

- `['finish_chat_turn']`：仅结束行动，没有发送消息
- `['send_chat_msg', 'finish_chat_turn']`：发送了一条消息后结束
- `['send_chat_msg', 'send_chat_msg', 'finish_chat_turn']`：发送了两条消息后结束

#### 2.5 确认调度停止原因

搜索以下关键词：

- `已达到最大轮次` → `turn_count >= max_turns`，正常结束
- `所有 AI 成员均已跳过发言` → 连续一整轮所有 AI 都 `has_content=False`
- `进入 IDLE 状态` → 房间停止调度

---

## 常见场景解析

### 场景 1：Agent 没说话（has_content=False）

**现象**：用户观察到某 Agent 在某轮没有发言。

**排查**：

1. 搜索 `结束本轮行动`，找到该 Agent 对应的日志
2. 如果 `has_content=False`，说明 LLM 主动选择不发言
3. 确认 `检测到工具调用` 日志 — 应该只有 `['finish_chat_turn']`

**结论**：这是 LLM 行为，不是调度 Bug。

### 场景 2：多房间消息交叉

**现象**：Agent 的 LLM 请求历史中，不同房间的消息交叉出现，看起来"顺序混乱"。

**排查**：

1. 确认 Agent 参与的房间列表
2. 在历史记录中识别不同房间的消息（消息格式包含房间名前缀）
3. 分别按房间梳理，确认每个房间内部的消息顺序是否正确

**结论**：多房间交叉是正常的。每个房间内部的消息顺序是严格保证的。

**LLM 历史记录示意：**

```
[Room-A] 用户1: 大家好
[Room-B] 用户2: 开始讨论吧
[Room-A] 用户3: 你好
[Room-B] 用户4: 我先说
```

上面看起来混乱，但按房间拆分后：
- Room-A: 用户1 → 用户3（顺序正确）
- Room-B: 用户2 → 用户4（顺序正确）

### 场景 3：read_index 已推进但 synced_count=0

**现象**：`同步房间消息` 日志显示 `raw > 0, own > 0, others == 0`。

**原因**：Agent 跨房间发送消息时（`send_chat_msg` 的 `room` 参数指向其他房间），消息会出现在目标房间的 `messages[]` 中。当该 Agent 在目标房间轮到自己时，`get_unread_messages` 返回这些消息，但 `pull_room_messages_to_history` 会过滤掉自己发的消息。

**结果**：`read_index` 被推进了，但没有新内容追加到历史 → `synced_count=0` → 自动跳过。

**这是正确行为**，不需要修复。

### 场景 4：轮次卡住

**现象**：Agent 的轮次一直没有结束，后续 Agent 无法行动。

**排查**：

1. 搜索 `agentService.log` 中对应 Agent 的最后日志
2. 检查是否有 `Agent 任务执行失败` 日志
3. 检查是否有 `LLM 调用失败` 相关错误
4. 确认 `agentTaskConsumer.py` 的 Consumer 状态（是否在运行）

**可能原因**：
- LLM 服务不可用或超时
- 工具执行异常
- Consumer 因异常停止

---

## 日志关键词速查表

| 关键词 | 文件 | 含义 |
|---|---|---|
| `同步房间消息` | agentService.log | Agent 拉取房间未读消息 |
| `无新消息，自动跳过本轮` | agentService.log | synced_count=0 时自动结束轮次 |
| `检测到工具调用` | agentService.log | LLM 返回的工具调用列表 |
| `结束本轮行动` | roomService.log | Agent 完成行动，含 has_content 标记 |
| `已达到最大轮次` | roomService.log | 房间达到 max_turns 停止 |
| `所有 AI 成员均已跳过发言` | roomService.log | 一整轮无人发言，停止 |
| `进入 IDLE 状态` | roomService.log | 房间停止调度 |
| `拒绝结束轮次申请` | roomService.log | 非当前发言人尝试结束轮次 |
| `自动跳过人类操作者回合` | roomService.log | OPERATOR 类型成员被自动跳过 |
| `Agent 任务执行失败` | agentService.log | Consumer 捕获到任务异常 |

---

## 辅助排查工具

### 查看房间当前状态

通过 API 获取房间实时状态：

```bash
curl http://127.0.0.1:8080/rooms/{team_name}/{room_key}.json | python3 -m json.tool
```

关注字段：
- `state`：INIT / SCHEDULING / IDLE
- `turn_pos`：当前发言位
- `turn_count`：当前轮次
- `current_turn_agent`：当前应发言的 Agent

### 查看 Agent 运行状态

```bash
curl http://127.0.0.1:8080/agents.json | python3 -m json.tool
```

关注字段：
- `status`：IDLE / BUSY / STOPPED
- `current_task`：当前执行的任务

### 抓取 LLM 请求记录

在 `config/setting.json` 中可配置 LLM 请求记录，用于离线分析 Agent 的 LLM 上下文完整历史。

---

## 总结

排查此类问题的核心原则：

1. **先看 `has_content`**：确认 Agent 是否真的没说话，还是 LLM 选择不说话
2. **分房间看顺序**：多房间交叉是正常的，按房间拆分后确认顺序
3. **看 raw/own/others**：消息同步的三个计数器能精确定位过滤情况
4. **看工具调用列表**：确认 LLM 实际调用了哪些工具
5. **先排除 LLM 行为，再怀疑调度 Bug**：绝大多数"消息丢失"都是 LLM 行为
