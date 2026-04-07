# Token Compaction（当前实现）

本文档描述仓库当前已落地的 token compact 机制。若代码与文档冲突，以代码为准。

涉及核心文件：

- `src/service/agentService/agentTurnRunner.py`
- `src/service/agentService/compact.py`
- `src/service/agentService/agentHistoryStore.py`
- `src/model/dbModel/historyUsage.py`
- `src/service/persistenceService.py`

## 1. 目标

当前实现解决三类问题：

1. 在发起 LLM 请求前，基于估算 token 提前触发 compact。
2. 在 assistant 成功返回后，基于实际 `usage.prompt_tokens` 再做一次 post-check compact。
3. 当模型实际返回 context overflow 类错误时，自动 compact 后重试一次。

范围限定：

- 支持 `DriverType.NATIVE`
- 支持 `DriverType.TSP`
- 不处理 `DriverType.CLAUDE_SDK`

## 2. 配置

配置位于 `llm_services[*]` 下，对应 `LlmServiceConfig`。

当前字段：

- `context_window_tokens`
- `reserve_output_tokens`
- `compact_trigger_ratio`
- `compact_summary_max_tokens`

触发阈值：

```text
hard_limit = context_window - reserve_output_tokens
trigger_tokens = floor(hard_limit * compact_trigger_ratio)
```

其中：

- `trigger_tokens` 用于决定是否执行 compact
- `hard_limit` 用于决定 compact 后是否仍然超限

## 3. 主流程

主入口在 `AgentTurnRunner._infer_to_item()`。

执行顺序：

1. 构造当前 `infer_messages`
2. 用 `compact.estimate_tokens(...)` 估算 prompt token
3. 调用 `_check_compact(..., check_stage="pre-check")`
4. 发起正常 `llmService.infer(...)`
5. 若返回 overflow 且本轮未触发 pre-check compact，则执行一次 compact retry
6. 正常响应时，先把 assistant 结果写入 history，并记录 usage
7. assistant 写入成功后，再调用 `_check_compact(..., check_stage="post-check")`
8. 若 post-check 触发 compact，则把同一条 assistant history 的 `usage.compact_stage` 回写为 `post`

## 4. `_check_compact`

`_check_compact(...)` 是统一的 compact 检测入口。

输入：

- `trigger_prompt_tokens`
- `estimated_tokens`
- `check_stage`

行为：

1. 若 `trigger_prompt_tokens < trigger_tokens`，直接返回
2. 否则执行一次 `_execute_compact()`
3. compact 失败时，抛出带阶段名的异常
4. compact 成功后，重建 infer 视图并重新估算 token
5. 若 compact 后估算仍 `>= hard_limit_tokens`，抛出带阶段名的异常

阶段说明：

- `pre-check`：基于请求前的估算 token 触发
- `post-check`：优先基于响应里的 `usage.prompt_tokens` 触发；若 usage 不可用，则退回估算值

## 5. Overflow Retry

overflow retry 是 compact 的兜底路径，只执行一次。

触发条件：

- `llmService.infer(...)` 返回失败
- 错误被 `compact.is_context_overflow_error(...)` 判定为上下文超长
- 本轮没有触发 `pre-check compact`

处理流程：

1. 执行一次 compact
2. 重建 infer 视图
3. 重新估算 token
4. 若 compact 后仍超限，则直接失败
5. 否则重试一次 `llmService.infer(...)`

## 6. History 形状

当前实现中，compact 的运行时形状已经简化为一条 `COMPACT_SUMMARY`。

compact 完成后，运行时内存窗口的核心形状为：

```text
[COMPACT_SUMMARY(user), 保留的尾部消息..., 当前 assistant/tool/user ...]
```

其中：

- `COMPACT_SUMMARY` 是一条带 `AgentHistoryTag.COMPACT_SUMMARY` 的 `USER` 消息
- 消息内容已经包含“以下是之前对话的压缩摘要...”这类恢复引导语
- `insert_compact_summary(...)` 完成后，会立即把内存窗口裁剪到最新 `COMPACT_SUMMARY`

## 7. Compact 源消息与插入位置

`AgentHistoryStore.build_compact_plan()` 负责决定两件事：

- 哪些消息需要被压缩
- `COMPACT_SUMMARY` 应该插到哪里

规则：

- 若尾部存在 pending infer 占位，先排除占位
- 若尾部不是 `USER`，压缩全部可见消息
- 若尾部是 `USER`，则从尾部向前跳过连续的 `USER` 消息，保留这段最新用户输入，把前缀压缩
- 若压缩前缀为空，则返回 `None`

`insert_seq` 指向第一条保留消息的 `seq`，因此 `COMPACT_SUMMARY` 会插在“保留尾部”之前。

## 8. Usage 记录

`agent_histories.usage` 当前字段包括：

- `estimated_prompt_tokens`
- `prompt_tokens`
- `completion_tokens`
- `total_tokens`
- `compact_stage`
- `overflow_retry`

`compact_stage` 取值：

- `none`：本轮未触发 compact
- `pre`：请求发出前触发了 compact
- `post`：assistant 已成功写入 history，随后触发了 post-check compact

注意：

- post-check 发生在 assistant 已成功落盘之后
- 因此若 post-check compact 失败，history 中仍保留该 assistant 的 `SUCCESS` 状态
- 但调用栈会继续抛异常给上层

## 9. 启动恢复

`persistenceService.load_agent_history_message()` 在恢复 history 时，会按最新 `COMPACT_SUMMARY` 裁剪窗口。

恢复策略：

- 若存在 `COMPACT_SUMMARY`，只保留最新 `COMPACT_SUMMARY` 及其之后的消息
- 若不存在 `COMPACT_SUMMARY`，则保留全部消息

这样可以保证：

- 运行时内存窗口与重启后的恢复窗口一致
- compact 前的旧前缀不会重新回到运行时 history

## 10. 已知时序特征

1. `pre-check` 与 `post-check` 都可能触发 compact，但 `compact_stage` 只记录本轮最终发生的阶段语义：`none / pre / post`。
2. 若本轮已经触发 `pre-check compact`，则 overflow retry 不会再次走 compact。
3. post-check 在 assistant 已写入 history 后执行，因此调用方需要接受“history 中已有成功回复，但当前调用仍抛异常”的时序。
