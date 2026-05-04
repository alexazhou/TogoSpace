# Agent 直接输出文字检测盲区分析

## 问题描述

当 Agent 在同一轮响应中**同时输出文字内容 + 调用 `finish_chat_turn`** 时，系统无法检测到"直接输出文字"的异常行为，导致：
1. 直接输出的文字被静默丢弃，不进入任何聊天室
2. 操作者（Operator）看不到 Agent 的回复
3. 不触发任何 hint 提醒，Agent 不知道自己的消息没发出去

## 复现场景

以项目经理"小马哥"为例：

```
操作者 → 小马哥（私聊）：分析下这个仓库作用

小马哥回复：
  text: "分析完成，以下是概要：TogoSpace 是多智能体协作框架..."
  tool_calls: [finish_chat_turn]

→ text 被丢弃，操作者看不到分析结果
→ 系统无任何提醒
```

## 代码追踪

### 1. 检测入口：`_infer_and_classify`（agentTurnRunner.py:235-244）

```python
async def _infer_and_classify(self, output_item, tools, tool_choice=None):
    assistant_message = await self._infer_to_item(output_item, tools, ...)
    
    if _detect_json_tool_call_in_content(assistant_message.content):
        # JSON 写入 content 的检测 → ERROR_ACTION
        ...
    elif assistant_message.tool_calls:
        return TurnStepResult.CONTINUE  # ← 有 tool_calls 就走这里
    else:
        return TurnStepResult.NO_ACTION  # ← 完全没有 tool_calls 才走这里
```

**关键问题**：只有当 `assistant_message.tool_calls` 为空时，才返回 `NO_ACTION`。只要响应中包含任何 tool_call（包括 finish_chat_turn），就返回 `CONTINUE`，直接文字被忽略。

### 2. Hint 触发条件：`_run_turn_loop`（agentTurnRunner.py:147-170）

```python
if result == TurnStepResult.NO_ACTION:
    failed_action_count += 1
    assistant_message = self._history.get_last_assistant_message()
    failure_kind = "direct_text" if assistant_message is not None \
        and len((assistant_message.content or "").strip()) > 0 else "no_action"
    
    if len(turn_setup.hint_prompt) > 0 and failed_action_count <= turn_setup.max_retries:
        # 注入 hint prompt 并重试
        await self._history.append_history_message(...)
        continue
    
    raise RuntimeError(...)
```

Hint 提示内容（`tspDriver.py:30-32`）：
```
"你必须通过调用工具来行动。如果你不需要发言，或者已经完成了所有行动，
请务必调用 finish_chat_turn 结束本轮（即跳过）。"
```

### 3. 为什么 TSP Driver 也没拦住

`tspDriver.py` 正确配置了 `hint_prompt` 和 `max_retries=3`，且 `host_managed_turn_loop=True`，走的是 `agentTurnRunner._run_turn_loop`。但由于上一步分类逻辑的盲区，`NO_ACTION` 永远不会被触发。

### 4. 对比 ClaudeSdkDriver 的处理

`claudeSdkDriver.py` 有独立的 `_consume_response_stream`，能检测到 `TextBlock`：
```python
if isinstance(block, TextBlock) and len(block.text.strip()) > 0:
    has_direct_text = True
```

并在 `_run_turn_sdk` 中有 `_REMINDER_PROMPT`：
```python
"【提醒】检测到你直接输出了文字。这些文字不会出现在聊天室中！..."
```

但这个提醒也有触发条件限制（需 `_turn_done=True` 且房间无新内容），存在类似盲区。

## 根因总结

| 场景 | 系统行为 | 是否触发 hint |
|------|---------|--------------|
| 纯文字，无 tool_calls | `NO_ACTION` → hint 注入 | ✅ 会触发 |
| 纯文字 + finish_chat_turn | `CONTINUE` → turn 正常结束 | ❌ 不触发 |
| 纯文字 + send_chat_msg + finish_chat_turn | `CONTINUE` → 正常 | ✅ 正常（文字是多余的但不影响） |
| 纯文字 + 其他 tool_calls | `CONTINUE` → 工具正常执行 | ❌ 不触发 |

**核心矛盾**：`_infer_and_classify` 的分类逻辑以"是否有 tool_calls"为唯一判断标准，没有区分"文字内容是 Agent 想发送给操作者的消息"和"文字只是思考过程的副产品"。

## 修复方案

### 方案 A：让 finish_chat_turn 执行时返回失败（推荐）

在 `_run_tool_to_item` 或 `funcToolService` 中，执行 finish_chat_turn 前检查：

**条件**：
1. 本轮 assistant 消息有实质性文字 content
2. 本轮未调用过 send_chat_msg

**行为**：finish_chat_turn 返回失败结果
```json
{
  "success": false,
  "message": "【提醒】检测到你直接输出了文字，这些文字不会出现在聊天室中。
              请使用 send_chat_msg 发送消息后再调用 finish_chat_turn。"
}
```

**优点**：改动小、逻辑直观、LLM 在下一轮推理时能看到错误信息并自动修正

**需要**：`AgentHistoryStore` 提供"本轮是否调用过 send_chat_msg"的查询能力

### 方案 B：在 `_infer_and_classify` 中增加分类

新增一个返回状态（如 `DIRECT_TEXT_BUT_FINISH`），当 content 有文字且 tool_calls 仅含 finish_chat_turn 时返回。在 `_run_turn_loop` 中特殊处理：注入 hint、不执行 finish。

**缺点**：逻辑耦合度高，需改动多处

### 方案 C：在 `_advance_step` 中拦截

执行 finish_chat_turn 前检查，如有直接文字→注入 USER 消息（REMINDER_PROMPT），跳过本次 finish 执行。

**缺点**：语义上不如方案 A 清晰

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/service/agentService/agentTurnRunner.py` | 核心循环逻辑，`_infer_and_classify`、`_run_turn_loop`、`_advance_step` |
| `src/service/agentService/driver/tspDriver.py` | TSP driver，`hint_prompt` 配置 |
| `src/service/agentService/driver/nativeDriver.py` | Native driver，`hint_prompt` 配置 |
| `src/service/agentService/driver/claudeSdkDriver.py` | Claude SDK driver，独立检测 + 提醒逻辑 |
| `src/service/agentService/agentHistoryStore.py` | 需要新增 `has_tool_call_in_turn()` 等方法 |
| `src/service/funcToolService/` | finish_chat_turn 工具的实现位置（方案 A 改动点） |

## 日期

2026-05-03
