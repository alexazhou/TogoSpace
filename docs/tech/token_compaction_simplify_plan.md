# Token Compaction 简化方案

本文档描述压缩流程的简化设计，将压缩变成纯内存行为，减少过程记录冗余。

## 1. 背景

### 1.1 当前压缩流程

当前 `_execute_compact` 执行时会写入 3 条消息：

```text
[旧可见前缀..., COMPACT_CMD(user), compact_summary(assistant), compact_context(user), 保留的消息...]
```

| 序号 | 消息 | Tag | 作用 |
|------|------|-----|------|
| 1 | `COMPACT_CMD(user)` | `COMPACT_CMD` | 压缩指令 |
| 2 | `compact_summary(assistant)` | 无 | LLM 原始摘要输出 |
| 3 | `compact_context(user)` | 无 | 供后续推理使用的恢复上下文 |

### 1.2 问题分析

1. **过程记录冗余**：消息 1 和 2 只是压缩过程记录，`build_infer_messages` 需要跳过它们
2. **runtime_window 处理复杂**：需要判断三段结构完整性
3. **失败处理硬性**：压缩失败直接抛异常，终止推理
4. **中间插入开销**：需要在 history 中间插入 3 条消息，涉及 seq 后移

## 2. 新方案

### 2.1 核心思路

将压缩变成一个**纯函数**行为：

```text
输入: messages (待压缩的历史消息)
输出: summary_text (包含引导语的摘要文本) 或 None
动作: 插入 1 条带 COMPACT_SUMMARY tag 的 user 消息
```

### 2.2 对比

| 对比项 | 当前方案 | 新方案 |
|--------|---------|--------|
| 写入消息数 | 3 条 | 1 条 |
| History Tag | `COMPACT_CMD` | `COMPACT_SUMMARY` |
| runtime_window 处理 | 跳过前 2 条 | 直接使用 |
| 压缩失败 | 抛异常终止 | 返回 None，可跳过 |
| 消息插入位置 | 中间（seq 后移） | 尾部追加 |

### 2.3 消息物理形状变化

当前：

```text
[旧消息..., COMPACT_CMD, summary, context, 保留消息, 后续...]
```

新方案：

```text
[旧消息..., COMPACT_SUMMARY(user), 后续...]
```

`COMPACT_SUMMARY` 消息内容已包含引导语："以下是之前对话的压缩摘要，请基于这些已知信息继续后续任务：..."

## 3. 具体改动

### 3.1 新增纯函数 `compact_messages`

位置：`compactPolicy.py` 或新建 `compactService.py`

```python
async def compact_messages(
    messages: list[OpenAIMessage],
    system_prompt: str,
    model: str,
    max_tokens: int = 2048,
) -> str | None:
    """压缩消息列表，返回摘要文本。

    Args:
        messages: 待压缩的消息列表
        system_prompt: 系统提示（用于正确理解上下文）
        model: 模型名称
        max_tokens: 摘要最大 token 数

    Returns:
        摘要文本（已包含引导语）或 None（压缩失败）
    """
    instruction = build_compact_instruction(max_tokens)
    ctx = GtCoreAgentDialogContext(
        system_prompt=system_prompt,
        messages=messages + [OpenAIMessage.user(instruction)],
        tools=None,
    )

    try:
        infer_result = await llmService.infer(model, ctx)
        if infer_result.ok is False or infer_result.response is None:
            return None

        summary = infer_result.response.choices[0].message.content or ""
        return build_compact_resume_prompt(summary)
    except Exception:
        return None
```

### 3.2 简化 `_execute_compact`

位置：`agentTurnRunner.py`

```python
async def _execute_compact(self) -> bool:
    """执行压缩，返回是否成功。"""
    compact_plan = self._history.build_compact_plan()
    if not compact_plan.source_messages or compact_plan.insert_seq is None:
        logger.warning("compact 跳过：无可压缩消息")
        return False

    _, llm_config, _, _ = self._resolve_compact_config()
    summary_text = await compact_messages(
        messages=compact_plan.source_messages,
        system_prompt=self.system_prompt,
        model=self.gt_agent.model,
        max_tokens=llm_config.compact_summary_max_tokens,
    )

    if summary_text is None:
        logger.warning("compact 失败：LLM 返回无效")
        return False

    # 尾部追加一条带 COMPACT_SUMMARY tag 的 user 消息
    await self._history.append_history_message(
        OpenAIMessage.user(summary_text),
        stage=AgentHistoryStage.INPUT,
        status=AgentHistoryStatus.SUCCESS,
        tags=[AgentHistoryTag.COMPACT_SUMMARY],
    )

    # 裁剪：保留 COMPACT_SUMMARY 及其之后的消息
    self._history.trim_to_compact_window()
    return True
```

### 3.3 调整 `_pre_check_compact`

```python
async def _pre_check_compact(...) -> tuple[...]:
    if estimated_tokens < trigger_tokens:
        return messages, estimated_tokens, False

    success = await self._execute_compact()
    if not success:
        # 压缩失败，跳过，继续推理（可能触发 overflow retry）
        return messages, estimated_tokens, False

    messages = self._history.build_infer_messages()
    estimated_tokens = compactPolicy.estimate_tokens(...)
    if estimated_tokens >= hard_limit_tokens:
        raise RuntimeError("compact 后仍超限")
    return messages, estimated_tokens, True
```

### 3.4 调整 `_build_runtime_window`

位置：`agentHistoryStore.py`

简化逻辑：找到 `COMPACT_SUMMARY` tag 作为边界，直接保留该消息及后续内容。

```python
def _build_runtime_window(self, ...) -> RuntimeWindow:
    # 找最新 COMPACT_SUMMARY
    compact_idx = self._find_latest_compact_summary_index()
    if compact_idx is None:
        return RuntimeWindow(start_index=None, items=list(self._items))

    # 直接保留 COMPACT_SUMMARY 及其之后的消息
    return RuntimeWindow(
        start_index=compact_idx,
        items=list(self._items[compact_idx:]),
    )

def _find_latest_compact_summary_index(self) -> int | None:
    for idx in range(len(self._items) - 1, -1, -1):
        if AgentHistoryTag.COMPACT_SUMMARY in self._items[idx].tags:
            return idx
    return None
```

### 3.5 调整 `build_compact_plan`

不再需要计算 `insert_seq`（改为尾部追加）：

```python
@dataclass
class CompactPlan:
    source_messages: list[OpenAIMessage]
    # 移除 insert_seq

def build_compact_plan(self) -> CompactPlan:
    runtime_window = self._build_runtime_window(exclude_pending_infer=True)
    preserve_start_idx = self._find_compact_preserve_start_index(runtime_window.items)
    if preserve_start_idx is None or preserve_start_idx <= 0:
        return CompactPlan(source_messages=[])

    return CompactPlan(
        source_messages=[item.openai_message for item in runtime_window.items[:preserve_start_idx]],
    )
```

### 3.6 Tag 更新

位置：`constants.py`

```python
class AgentHistoryTag:
    # 移除 COMPACT_CMD
    COMPACT_SUMMARY = "compact_summary"  # 新增
```

### 3.7 持久化恢复

位置：`persistenceService.py`

恢复时只加载 `COMPACT_SUMMARY` 及其之后的消息。

## 4. 失败处理策略

| 场景 | 当前方案 | 新方案 |
|------|---------|--------|
| pre-check 压缩失败 | 抛异常终止 | 返回 False，跳过，继续推理 |
| overflow retry 压缩失败 | 抛异常终止 | 抛异常（已无路可退） |

新方案更优雅：
- pre-check 压缩失败不影响流程，继续推理（可能触发 overflow retry 补救）
- overflow retry 压缩失败才终止（此时已无其他选择）

## 5. 影响范围

| 文件 | 改动类型 |
|------|---------|
| `compactPolicy.py` | 新增 `compact_messages` 纯函数 |
| `agentTurnRunner.py` | 简化 `_execute_compact`、`_pre_check_compact` |
| `agentHistoryStore.py` | 简化 `_build_runtime_window`、`build_compact_plan` |
| `constants.py` | Tag 更新 |
| `persistenceService.py` | 恢复逻辑调整 |
| `tests/unit/test_infer_compact.py` | 测试更新 |

## 6. 风险评估

1. **摘要质量**：新方案的摘要内容与当前一致，只是合并了 3 条消息为 1 条
2. **向后兼容**：需要处理旧数据中可能存在的 `COMPACT_CMD` tag
3. **性能影响**：尾部追加比中间插入更高效，无需 seq 后移

## 7. 实施步骤

1. 新增 `COMPACT_SUMMARY` tag
2. 实现 `compact_messages` 纯函数
3. 简化 `_execute_compact`
4. 简化 `_build_runtime_window` 和 `build_compact_plan`
5. 更新持久化恢复逻辑
6. 更新测试
7. 移除 `COMPACT_CMD` tag（可选，保留向后兼容）