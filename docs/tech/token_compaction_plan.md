# Token Compaction 设计与实现

本文档描述当前 `native` / `tsp` driver 下的 token 预算与自动 compact 实现。内容以代码现状为准。

涉及核心文件：

- `src/service/agentService/agentTurnRunner.py`
- `src/service/agentService/agentHistoryStore.py`
- `src/service/agentService/compactPolicy.py`
- `src/dal/db/gtAgentHistoryManager.py`
- `src/service/persistenceService.py`
- `src/util/configTypes.py`

## 1. 目标

当前实现解决三个问题：

1. 在发起 LLM 请求前估算上下文 token，提前触发 compact。
2. 在推理记录中保留最基本的 usage 数据，便于观察上下文压力。
3. 当模型实际返回 `context overflow` 类错误时，自动 compact 后重试一次。

范围限定：

- 支持 `DriverType.NATIVE`
- 支持 `DriverType.TSP`
- 不处理 `DriverType.CLAUDE_SDK`

## 2. 配置

配置位于 `llm_services[*]` 下，对应类型是 [configTypes.py](/Volumes/PDATA/GitDB/TeamAgent/src/util/configTypes.py) 中的 `LlmServiceConfig`。

当前字段：

- `context_window_tokens`
- `reserve_output_tokens`
- `compact_trigger_ratio`
- `compact_summary_max_tokens`

默认值：

- `context_window_tokens = 32000`
- `reserve_output_tokens = 4096`
- `compact_trigger_ratio = 0.85`
- `compact_summary_max_tokens = 2048`

上下文窗口解析规则在 [compactPolicy.py](/Volumes/PDATA/GitDB/TeamAgent/src/service/agentService/compactPolicy.py)：

1. 优先使用内置 `DEFAULT_MODEL_CONTEXT_WINDOWS`
2. 找不到时回退到配置里的 `context_window_tokens`

compact 触发阈值：

```text
hard_limit = context_window - reserve_output_tokens
trigger_tokens = floor(hard_limit * compact_trigger_ratio)
```

## 3. 总体职责分布

### 3.1 `compactPolicy`

职责：

- 解析模型上下文窗口
- 计算 compact 触发阈值
- 做 token 估算
- 识别 overflow 错误
- 生成 compact 指令和 compact 后的摘要上下文消息
- 构造 `usage_json`

它是纯函数模块，不直接读写 history，也不直接发请求。

### 3.2 `AgentHistoryStore`

职责：

- 管理内存中的 agent history
- 提供“本次 infer 实际要发送的消息视图”
- 计算 compact 的源消息范围
- 决定 `COMPACT_CMD` 的插入位置
- 支持按 `seq` 在历史中间插入消息
- 在 compact 完成后裁剪内存窗口

### 3.3 `AgentTurnRunner`

职责：

- 编排 `_infer()` 主流程
- 执行 `pre_check`
- 处理 overflow retry
- 执行 compact 流程

### 3.4 `gtAgentHistoryManager`

职责：

- 持久化 history
- 支持按 `seq` 整体后移记录
- 支持在指定 `seq` 插入新 history

### 3.5 `persistenceService`

职责：

- 进程启动时恢复 history
- 按 compact 规则裁剪恢复窗口，避免把 compact 前无效历史重新加载回内存

## 4. `_infer()` 主流程

主入口在 [agentTurnRunner.py](/Volumes/PDATA/GitDB/TeamAgent/src/service/agentService/agentTurnRunner.py) 的 `_infer()`。

执行顺序：

1. `history.assert_infer_ready(...)`
2. `history.build_messages_for_infer()`
3. `compactPolicy.estimate_tokens(...)`
4. 执行 `pre_check`
5. 复用尾部 pending infer，或新建一条 `INFER + INIT`
6. 调用 `llmService.infer(...)`
7. 如果是 overflow，执行一次 compact 后 retry once
8. 成功则写 `SUCCESS + usage_json`
9. 失败则统一写 `FAILED + usage_json`

当前没有 `post_check`。

## 5. Pre-check

`pre_check` 的含义是“请求前检查”。

逻辑：

1. 根据当前 infer 视图估算 prompt token
2. 若 `estimated_tokens < trigger_tokens`，直接发送请求
3. 若 `estimated_tokens >= trigger_tokens`，先执行 compact
4. compact 后重新构造 infer 视图并再次估算
5. 若 compact 后仍然超限，直接失败

当前“compact 后仍然超限”的判断仍使用 `should_fail_after_compact(...)`。

## 6. Overflow Retry

这是 `pre_check` 之外的兜底路径。

触发条件：

- `llmService.infer(...)` 返回失败
- 错误被 `compactPolicy.is_context_overflow_error(...)` 识别为上下文超长
- 本次请求不是 compact 自身触发的 `_skip_compact=True` 请求
- 本次请求之前没有触发 `pre_check`

处理流程：

1. 执行一次 compact
2. 重新构造 infer 视图
3. 重新估算 token
4. 若 compact 后仍超限，直接失败
5. 否则重新调用一次 `llmService.infer(...)`

当前 overflow 只重试一次。

## 7. History 物理形状

compact 不再简单 append 到 history 末尾，而是按 `seq` 插入到中间。

compact 完成后，history 的物理形状如下：

```text
[旧可见前缀..., COMPACT_CMD(user), compact_summary(assistant), compact_context(user), 保留的最后一条消息, 后续消息...]
```

其中：

- `COMPACT_CMD(user)` 是压缩命令，带 `AgentHistoryTag.COMPACT_CMD`
- `compact_summary(assistant)` 是模型直接返回的原始压缩结果
- `compact_context(user)` 是从 `compact_summary` 转换出来、供后续继续推理使用的上下文消息
- “保留的最后一条消息”是 compact 时不纳入摘要的那条原始消息

这三条消息的插入由 `seq` 驱动，后面的原始消息会整体后移。

## 8. Compact 源消息与插入位置

### 8.1 Compact 源消息

[agentHistoryStore.py](/Volumes/PDATA/GitDB/TeamAgent/src/service/agentService/agentHistoryStore.py) 中的 `build_compact_source_messages()` 负责提取待压缩源消息。

规则：

- 基于当前“可见 history”工作
- 保留最后一条可见消息原文
- 将它之前的可见消息全部作为 compact 源
- 如果尾部是 pending infer 占位，则先排除该占位

换句话说，compact 压缩的是“当前可见上下文的前缀”。

### 8.2 插入位置

`find_compact_insert_seq()` 返回 `COMPACT_CMD` 要插入的 `seq`。

规则：

- 插在当前最后一条保留消息之前

因此：

- `COMPACT_CMD` 前面是待压缩前缀
- `COMPACT_CMD` 后面保留原始最后一条消息及后续消息

## 9. 中间插入实现

### 9.1 Store 层

[agentHistoryStore.py](/Volumes/PDATA/GitDB/TeamAgent/src/service/agentService/agentHistoryStore.py) 新增：

- `insert_history_message_at_seq(...)`
- `find_compact_insert_seq()`

`insert_history_message_at_seq(...)` 的逻辑：

1. 构造新 `GtAgentHistory`
2. 调用 DAL 在 DB 中执行 `seq` 后移 + 插入
3. DB 成功后，再更新内存中的 `_items`

### 9.2 DAL 层

[gtAgentHistoryManager.py](/Volumes/PDATA/GitDB/TeamAgent/src/dal/db/gtAgentHistoryManager.py) 新增：

- `shift_agent_history_seq_from(agent_id, from_seq, delta)`
- `insert_agent_history_message_at_seq(message)`

实现策略：

- 插入前先将 `seq >= from_seq` 的记录整体后移
- `delta > 0` 时按 `seq desc` 顺序更新，尽量规避 `(agent_id, seq)` 唯一索引冲突
- 后移完成后再插入新记录

## 10. Compact 执行流程

具体实现位于 [agentTurnRunner.py](/Volumes/PDATA/GitDB/TeamAgent/src/service/agentService/agentTurnRunner.py) 的 `_execute_compact()`。

执行顺序：

1. `source_messages = history.build_compact_source_messages()`
2. `insert_seq = history.find_compact_insert_seq()`
3. 在 `insert_seq` 插入 `COMPACT_CMD(user)`
4. 直接调用 `llmService.infer(...)`，输入为：
   - `source_messages + [COMPACT_CMD]`
5. 将模型返回写成 `compact_summary(assistant)`，插入到 `insert_seq + 1`
6. 将摘要包装成 `compact_context(user)`，插入到 `insert_seq + 2`
7. 调用 `history.drop_messages_before_latest_compact()`

compact 本身不再递归调用 `_infer(_skip_compact=True)`，而是直接调用 `llmService.infer(...)`。

## 11. Infer 视图规则

[agentHistoryStore.py](/Volumes/PDATA/GitDB/TeamAgent/src/service/agentService/agentHistoryStore.py) 的 `build_infer_messages()` 负责对物理 history 做逻辑视图转换。

规则如下。

### 11.1 没有 compact

返回全部消息。

### 11.2 compact 已完成

判断条件：

- 最新 `COMPACT_CMD` 后面存在两条消息
- 第一条是 `INFER + SUCCESS`
- 第二条是 `INPUT + USER`

满足时，视图中：

- 丢弃 `COMPACT_CMD`
- 丢弃 `compact_summary(assistant)`
- 从 `compact_context(user)` 开始继续往后取

### 11.3 compact 未完成

如果最新 `COMPACT_CMD` 尚未形成完整三段结构，则 infer 视图忽略 `COMPACT_CMD` 本身，不把它作为有效上下文继续发送。

## 12. Pending Infer 复用

`_infer()` 已经不再接收 `resume_item` 参数。

当前策略：

- 若 history 尾部是 `INFER + INIT/FAILED`，视为 pending infer
- 本次 `_infer()` 复用这条记录
- `build_messages_for_infer()` 会自动将这条尾部占位 infer 从输入消息中排除

这样可以避免续跑时多生成一条新的 `INFER` 记录。

## 13. Usage 记录

usage 会写入 `agent_histories.usage_json`。

当前字段：

- `estimated_prompt_tokens`
- `prompt_tokens`
- `completion_tokens`
- `total_tokens`
- `pre_check_triggered`
- `overflow_retry`

构造位置在 [compactPolicy.py](/Volumes/PDATA/GitDB/TeamAgent/src/service/agentService/compactPolicy.py) 的 `build_usage_payload(...)`。

## 14. 启动恢复

[persistenceService.py](/Volumes/PDATA/GitDB/TeamAgent/src/service/persistenceService.py) 在加载 agent history 时会调用 `get_compact_window_start_index()`。

当前恢复策略：

- 如果存在 `COMPACT_CMD`
- 只保留最新 `COMPACT_CMD` 及其之后的消息进入内存

这样可以保证：

- compact 前的无效前缀不会重新加载
- 重启后的内存视图和运行期 compact 后的内存窗口保持一致

## 15. 已知限制

当前实现仍有几个需要后续继续收敛的点：

1. `insert_agent_history_message_at_seq(...)` 目前没有显式事务包裹
   这意味着“批量平移 seq + 插入”仍然可以继续增强为原子操作。

2. `should_fail_after_compact(...)` 仍需确认是否严格按 `hard_limit` 判断
   否则可能出现“compact 后仍高于 trigger，但尚未真正超过 hard limit”的误判。

3. `compact 未完成` 的恢复语义还可以继续收紧
   当前已经能跳过 `COMPACT_CMD` 本身，但中途崩溃时的恢复行为仍值得继续验证。

4. 当前没有对连续 `user` 消息做合并
   第一版默认依赖大多数 OpenAI-compatible 服务能够接受连续 `user` 消息。

## 16. 相关文档

- [token_compaction_review_findings.md](/Volumes/PDATA/GitDB/TeamAgent/docs/tech/token_compaction_review_findings.md)

该文档记录的是 review 过程中发现的问题清单；本文档描述的是当前实现结构与运行机制。
