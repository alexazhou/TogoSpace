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

`context_window` 取值优先级：

```text
模型级配置 > 全局配置 > 代码默认值
```

- 开箱模型的窗口由配置模板（`assets/config_template.json`）中的模型级 `context_config` 提供；例如 `qwen-plus` 为 `131072`、`qwen-vl-plus` 为 `32768`。
- 模型级 `context_config` 中，字段值等于代码默认值的项视为未显式设置，继承全局值；全局也未设置时使用代码默认值 `262144`。
- 不再维护系统内置模型表，模型名不参与窗口解析。

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

1. 调用 `_check_compact(check_stage="pre-check")`，内部通过 `_token_snapshot()` 取 token 口径
2. 构造当前 `infer_messages`（compact 可能已裁剪历史，必须在之后重建）
3. 发起正常 `llmService.infer(...)`
4. 若返回 overflow 且本轮未触发 pre-check compact，则执行一次 compact retry
5. 正常响应时，先把 assistant 结果写入 history，并记录 usage
6. assistant 写入成功后，再调用 `_check_compact(check_stage="post-check")`
7. 若 post-check 触发 compact，则把同一条 assistant history 的 `usage.compact_stage` 回写为 `post`

### 3.1 token 口径：`HistoryTokenSnapshot`

token 长度**只有一个来源**：`AgentHistoryStore.token_snapshot(model, system_prompt, tools)`。
调用方（compact 判定、活动记录、上下文占用、手动压缩）都经
`AgentTurnRunner._token_snapshot()` 取，不再各自拼消息列表去 tokenize。

快照字段：

| 字段 | 含义 |
| --- | --- |
| `estimated_tokens` | 全量本地估算：system prompt + tools + 全部未压缩消息（不含 pending 占位） |
| `current_tokens` | 当前上下文长度口径（下详） |
| `accurate_usage` | 最近一次上游返回的真实 usage（从 history 还原，`prompt_cache_usage` 恒为 `None`） |
| `accurate_anchor_seq` | 该 usage 对应的锚点行 `seq` |

`current_tokens` 计算规则：

```text
无可用锚点：current_tokens = estimated_tokens
有可用锚点：current_tokens = anchor.usage.prompt_tokens + estimate(锚点行自身及其后消息)
```

tail **从锚点行自身开始**：真值只覆盖锚点之前的输入，锚点的 assistant 回复也要计入当前上下文。
`tools` 参与全量估算（`litellm.token_counter(..., tools=...)`），保证新会话 / compact 后
没有真值锚点时估算不漏掉工具定义。

### 3.2 真值锚点与会话边界

从 `_items` 尾部向前找满足以下条件的最近一行：

- `item.usage is not None` 且 `item.usage.prompt_tokens is not None`；
- 该行属于当前 session：`item.id > summary.id`，`summary` 是尾部向前第一条
  `COMPACT_SUMMARY` 行。

第二条**不能退化成「列表顺序在 summary 之后」**：`insert_compact_summary()` 会保留一段尾部
旧行，它们顺序上排在 summary 之后，但 id 更小、`usage` 描述的是压缩前的上下文：

```text
_items = [COMPACT_SUMMARY(id=100), ASSISTANT(id=50, usage=压缩前), USER(id=51), ...]
                                        ^ 列表在 summary 之后，但 id=50 < 100，不能当锚点
```

### 3.3 快照缓存

缓存 key = `(model, system_prompt, tools_fingerprint, self._revision)`，只保留最后一份。
六个写操作递增 `_revision` 使缓存失效：

- `append_history_message`
- `finalize_history_item`
- `insert_compact_summary`
- `replace`
- `reload_from_db`
- `invalidate_token_cache`（视觉兜底回填 recognition 等原地修改）

## 4. `_check_compact`

`_check_compact(check_stage=...)` 是统一的 compact 检测入口。

输入只有阶段名 —— **不再接收任何 token 数字**，长度口径由 `_token_snapshot()` 现取。

行为：

1. 取快照；若 `snapshot.current_tokens < trigger_tokens`，直接返回 `(snapshot, False)`
2. 否则执行一次 `_execute_compact(pre_snapshot=snapshot)`
3. compact 失败时，抛出带阶段名的异常
4. compact 成功后，重建 infer 视图（日志用）并取压缩后的新快照
5. 若压缩后 `current_tokens >= hard_limit_tokens`，抛出带阶段名的异常
6. 广播压缩后的用量（复用同一份快照，不重复跑 tokenizer）
7. 返回 `(post_snapshot, True)`

阶段说明：

- `pre-check`：基于请求前的快照触发
- `post-check`：assistant 已落库，history 里自然有最新真值锚点，因此 post-check
  与 pre-check 走**完全相同的口径**，不再区分「有 usage 用 usage、没有用估算」

`_execute_compact(pre_snapshot=None)` 返回 `HistoryTokenSnapshot`（不再返回 int）。
压缩前后的数字都从快照取，`context_tokens_before` / `context_tokens_after`
活动元数据与 `current_tokens` 同源。

## 5. Overflow Retry

overflow retry 是 compact 的兜底路径，只执行一次。

触发条件：

- `llmService.infer(...)` 返回失败
- 错误被 `compact.is_context_overflow_error(...)` 判定为上下文超长
- 本轮没有触发 `pre-check compact`

处理流程：

1. 执行一次 compact（`pre_snapshot` 传入当前快照，避免重复跑 tokenizer）
2. 重建 infer 视图
3. 取压缩后的新快照
4. 若 `current_tokens` 仍 `>= hard_limit`，则直接失败
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
- 从尾部向前跳过连续的 `USER` 消息；这些最新用户输入属于保留区，不进入 compact source
- 在“去掉末尾 `USER`”后的视图上，若尾部存在未完成的 tool call 链，则从对应的 `ASSISTANT(tool_calls...)` 起整体保留，不进入 compact source
- 这里的“未完成”指 assistant 声明的 `tool_calls` 尚未全部在后续 `TOOL(tool_result)` 消息中闭合
- 若某段 `ASSISTANT(tool_calls...) -> TOOL(tool_result)...` 已完整闭合，则它属于稳定历史，可以被 compact
- 若压缩前缀为空，则返回 `None`

因此，compact source 始终只包含“稳定前缀”：

- 不包含末尾最新用户消息
- 不包含未完成工具调用尾巴

`insert_seq` 指向第一条保留消息的 `seq`；若不存在保留尾部，则 `COMPACT_SUMMARY` 插入到当前窗口起点。

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

口径说明：

- `estimated_prompt_tokens` 存的是**本次请求发出前**的 `snapshot.current_tokens`；
- `prompt_tokens` / `completion_tokens` / `total_tokens` 仍是本次请求的上游真值；
- 活动 metadata 的 `estimated_prompt_tokens` 与 history 同源。

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
