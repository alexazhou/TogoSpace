# Tool/Tool Result 消息配对问题

## 问题概述

当 Agent 执行 tool_call 过程中被用户手动取消时，会导致 OpenAI API 规范要求的 tool_call 与 tool_result 消息配对关系被破坏。

## 问题现象

### 日志中的实际案例

从 `dev_storage_root/logs/backend/util/llm_api.log` 中可以看到：

```
175: ASSISTANT (tool_calls=['tool-119a367f423a4788964255e457b1b85d'])
176: USER (content=本轮任务已被操作者中断...)  ← 没有 TOOL response!
177: USER (content=当前轮到你行动...)
```

ASSISTANT 消息声明了 tool_call，但紧接着没有对应的 TOOL 消息作为响应，直接跳到了 USER 消息。

## 根因分析

### 1. CancelledError 穿透 execute_tool_call

在 `src/service/agentService/toolRegistry.py` 的 `execute_tool_call` 方法中：

```python
# Line 77
except Exception as e:
    # 只捕获 Exception，但 CancelledError 是 BaseException
```

`CancelledError` 是 `BaseException` 的子类而非 `Exception`，因此会穿透这个异常捕获层，导致 tool 执行流程被中断。

### 2. TOOL 记录 message 字段为 NULL

当 tool 执行被 CancelledError 中断后，对应的 TOOL 记录虽然会被创建，但其 `message` 字段为 `NULL`（因为结果未正常填充）。

### 3. build_infer_messages 跳过 CANCELLED TOOL

在 `src/service/agentService/agentHistoryStore.py` 中：

```python
def build_infer_messages(self) -> list[llmApiUtil.OpenAIMessage]:
    """构造本次 _infer() 真正发给模型的消息列表。"""
    items = list(self._items)
    if self.get_pending_infer_item() is not None:
        items = items[:-1]
    return [item.openai_message for item in items if item.has_message]
```

`has_message` 检查会过滤掉 `message=NULL` 的记录，导致 CANCELLED 状态的 TOOL 记录被跳过。

### 4. 最终结果：tool_call 无对应 tool_result

构造给 LLM 的消息序列中，ASSISTANT 消息带有 `tool_calls`，但缺少对应的 TOOL 消息响应。这违反了 OpenAI API 规范。

## 当前影响

- GLM-5 模型对这种不配对情况有容忍度，暂未报错
- 其他严格遵守 OpenAI API 规范的模型可能会拒绝处理或返回错误

## 潜在解决方案

### 方案 A：在 build_infer_messages 中补写占位 TOOL 消息

当检测到 ASSISTANT 消息有未配对的 tool_call 时，自动补充一条 TOOL 消息（内容为 "cancelled by user"）。

**优点**：
- 不改变现有流程
- 保证消息配对合规

**缺点**：
- 每次推理前都要检查配对关系，增加计算开销

### 方案 B：在 finalize_cancel_turn 时修复历史

在取消 turn 时，确保所有未完成的 tool_call 都有对应的 TOOL 记录，且 message 字段填充有效内容。

**优点**：
- 一次性修复，不影响正常流程性能

**缺点**：
- 需要修改 cancel 逻辑，可能影响现有取消流程的稳定性

### 方案 C：execute_tool_call 捕获 BaseException

修改 `execute_tool_call` 的异常捕获范围，将 CancelledError 也纳入处理，确保 tool 执行结果被正确记录。

**优点**：
- 从根源解决问题

**缺点**：
- CancelledError 在 asyncio 中有特殊语义，捕获后可能影响其他取消流程

## 相关代码位置

- `src/service/agentService/toolRegistry.py:77` - execute_tool_call 异常捕获
- `src/service/agentService/agentHistoryStore.py:307` - build_infer_messages
- `src/service/agentService/agentHistoryStore.py:254` - finalize_cancel_turn

## 状态

- **发现时间**：2026-04-27
- **当前状态**：待解决
- **紧急程度**：中（当前 GLM-5 可容忍，但未来可能切换模型）