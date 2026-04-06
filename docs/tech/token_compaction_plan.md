# Token 预算与自动压缩方案

本文档用于讨论 `native` / `tsp` driver 下的上下文 token 预算、超长检测与自动压缩方案。

当前约束：

- `claude_sdk` 不纳入本次方案。其底层已有内置压缩能力。
- 本次优先不新增独立 service，先收敛到现有几个核心类：
  - `src/service/agentService/agentHistoryStore.py`
  - `src/service/agentService/agentTurnRunner.py`
  - `src/service/llmService.py`
  - `src/util/llmApiUtil/OpenAiModels.py`

---

## 1. 目标

目标有三个：

1. 在请求发出前估算当前上下文 token，提前判断是否需要压缩。
2. 在请求成功后记录底层 usage token，作为观测与后续阈值校准依据。
3. 在请求失败且错误属于“上下文超长”时，自动压缩后重试一次。

本次方案只要求支持：

- `DriverType.NATIVE`
- `DriverType.TSP`

不处理：

- `DriverType.CLAUDE_SDK`

---

## 2. 设计结论

当前倾向采用最小改动方案，不新增独立 `contextBudgetService` / `historyCompactionService`。

### 2.1 职责分布

#### `AgentHistoryStore`

负责：

- 提供 token 估算相关静态方法
- 提供“构造本次 infer 输入消息”的方法
- 提供“找到可压缩历史范围”的方法
- 提供“生成 compact 后上下文视图”的方法

不负责：

- 直接发起 LLM 请求
- 决定何时重试

建议新增的方法如下。

##### 1. `build_infer_messages() -> list[llmApiUtil.OpenAIMessage]`

用途：

- 构造当前这次 `_infer()` 真正要发给模型的消息列表
- 替代当前直接使用 `export_openai_message_list()`
- 与内存裁剪规则保持一致

规则：

- 若 history 中不存在 `COMPACT_CMD`，返回全部消息
- 若存在 `COMPACT_CMD`，只返回“最新一条 `COMPACT_CMD` 及其之后的消息”

##### 2. `find_latest_compact_index() -> int | None`

用途：

- 找到最新一条带 `AgentHistoryTag.COMPACT_CMD` 的 history 下标
- 供 `build_infer_messages()` 和 compact 逻辑复用

规则：

- 从尾部向前扫描
- 找到第一条带 `COMPACT_CMD` tag 的消息即返回
- 若不存在则返回 `None`

##### 3. `build_compact_source_messages() -> list[llmApiUtil.OpenAIMessage]`

用途：

- 生成 compact 时要送给模型的“原始历史输入”

规则：

- 以当前最后一条消息为边界，且这条最后消息就是压缩命令
- 返回这条压缩命令之前的全部消息
- 这条压缩命令本身不参与 compact

##### 4. `build_post_compact_messages(compact_message: llmApiUtil.OpenAIMessage) -> list[llmApiUtil.OpenAIMessage]`

用途：

- 生成 compact 之后的新上下文视图
- 供 compact 完成后再次 `_infer()` 使用

规则：

- 使用新生成的 `compact_message` 作为压缩后的历史入口
- 原始最后一条消息是压缩命令，不保留到压缩后的上下文视图中
- 以及 compact 之后新增的后续消息

备注：

- 这个方法更偏“视图构造”
- 不一定要求直接写回 history，可以先用于内存态验证

##### 4.1 `drop_messages_before_latest_compact() -> None`

用途：

- compact 完成后，直接裁剪 `AgentHistoryStore` 的内存消息

规则：

- 找到最新一条 `COMPACT_CMD`
- 删除该消息之前的全部内存 history item
- 保留这条 `COMPACT_CMD` 及其之后的消息

说明：

- 这是内存态裁剪，不影响数据库中已持久化的完整历史
- 这样后续 `_infer()`、工具执行和 turn 恢复都只面向 compact 之后的内存视图

##### 5. `estimate_tokens(...) -> int`

建议签名：

```python
@staticmethod
def estimate_tokens(
    *,
    model: str,
    system_prompt: str,
    messages: list[llmApiUtil.OpenAIMessage],
    tools: list[llmApiUtil.OpenAITool] | None,
) -> int:
```

用途：

- 估算某次请求的输入 token 数

规则：

- 估算对象必须包含：
  - `system_prompt`
  - `messages`
  - `tools`
- 复用 `litellm.token_counter(...)`

##### 6. `build_usage_payload(...) -> dict[str, object]`

建议签名：

```python
@staticmethod
def build_usage_payload(
    *,
    model: str,
    context_window_tokens: int,
    reserve_output_tokens: int,
    compact_trigger_tokens: int,
    estimated_prompt_tokens: int | None,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    total_tokens: int | None,
    pre_check_triggered: bool,
    post_check_triggered: bool,
    overflow_retry: bool,
) -> dict[str, object]:
```

用途：

- 统一构造要写入 `usage_json` 的数据
- 避免 `AgentTurnRunner` 里手搓字典

##### 7. `should_trigger_pre_check_compact(...) -> bool`

建议签名：

```python
@staticmethod
def should_trigger_pre_check_compact(
    *,
    estimated_prompt_tokens: int,
    compact_trigger_tokens: int,
) -> bool:
```

用途：

- 判断请求前是否需要 compact

规则：

- `estimated_prompt_tokens >= compact_trigger_tokens` 时返回 `True`

##### 8. `should_trigger_post_check_compact(...) -> bool`

建议签名：

```python
@staticmethod
def should_trigger_post_check_compact(
    *,
    assistant_message: llmApiUtil.OpenAIMessage,
    prompt_tokens: int | None,
    compact_trigger_tokens: int,
) -> bool:
```

用途：

- 判断 assistant 返回后，是否要跳过工具执行，直接 compact

规则：

- `assistant_message.tool_calls` 非空
- 且 `prompt_tokens` 不为 `None`
- 且 `prompt_tokens >= compact_trigger_tokens`

同时满足时返回 `True`

##### 9. `should_fail_after_compact(...) -> bool`

建议签名：

```python
@staticmethod
def should_fail_after_compact(
    *,
    estimated_prompt_tokens: int,
    hard_limit_tokens: int,
) -> bool:
```

用途：

- compact 后再次估算时，判断是否仍然超长并直接失败

规则：

- `estimated_prompt_tokens >= hard_limit_tokens` 时返回 `True`

##### 10. `build_compact_prompt(source_messages: list[llmApiUtil.OpenAIMessage]) -> str`

用途：

- 把待压缩历史包装成 compact 请求用的 prompt 文本

规则：

- 输出的是“发给同一模型做压缩”的输入 prompt
- 结构应与第 8 节的 compact prompt 模板保持一致

##### 11. `build_compact_history_message(compact_text: str) -> llmApiUtil.OpenAIMessage`

用途：

- 把 compact 结果封装为一条可写入 history 的消息

规则：

- role 固定为 `user`
- 内容为 compact 生成的摘要文本
- 这条消息是 compact 之后真正写入 history、并会在后续再次发给模型的输入消息
- 入库时配合 `AgentHistoryTag.COMPACT_CMD`
- `COMPACT_CMD` 不打在模型返回的 assistant 压缩结果上

##### 12. `append_compact_message(...) -> GtAgentHistory`

建议签名：

```python
async def append_compact_message(
    self,
    compact_text: str,
    *,
    usage_json: dict[str, object] | None = None,
) -> GtAgentHistory:
```

用途：

- 把 compact 结果正式写入 history

规则：

- 内部调用 `append_history_message(...)`
- 自动附加 `AgentHistoryTag.COMPACT_CMD`
- tag 只附加在这条 compact 用户消息上，不附加在压缩请求的 assistant 响应上
- 若最终决定 `usage_json` 只给 `INFER` 存，则这里可以不写 usage
- 成功追加后，应立即配合 `drop_messages_before_latest_compact()` 裁剪内存态

#### `AgentTurnRunner`

负责：

- `_infer()` 前执行 pre_check token 检查
- 触发 compact
- 在 context overflow 错误时执行 compact + retry once
- 在请求成功后记录 usage 和压缩信号

#### `llmService`

负责：

- 调用底层模型
- 返回 usage
- 统一识别“上下文超长”异常

### 2.2 最小实现范围

如果希望第一版尽快落地，建议按下面范围做：

1. 配置里增加 context budget 相关字段
2. `OpenAIResponse` 增加 usage
3. `llmService` 增加 overflow error 判断
4. `AgentHistoryStore` 增加：
   - token estimate 静态方法
   - latest compact 查找
   - build infer messages
   - compact prompt / compact message 构造
   - compact 后内存裁剪
   - usage_json 构造
5. `AgentTurnRunner._infer()` 增加：
   - pre_check compact
   - compact 后再次估算，若仍超长则直接失败
   - assistant 返回 `tool_calls` 且 post_check 命中时，跳过工具执行，直接 compact 后重新 infer
   - overflow retry once
6. `agent_histories` 增加 `usage_json` 列，并在 `INFER` 阶段写入 usage 数据
7. compact 结果作为一条带 `COMPACT_CMD` tag 的 `user` 消息写入 history
8. 启动恢复时，若存在 `COMPACT_CMD`，只把最新 `COMPACT_CMD` 及其之后的消息加载进 `AgentHistoryStore`

第一版可以先不做：

- websocket 实时推送 token 数据
- 前端展示 token 曲线

---

## 3. 配置方案

在 `config/setting.json` 的 `llm_services[*]` 下新增上下文预算配置。

建议字段：

```json
{
  "llm_services": [
    {
      "name": "dashscope",
      "enable": true,
      "model": "glm-4.7",
      "base_url": "https://xxx/v1/chat/completions",
      "api_key": "xxx",
      "type": "openai-compatible",
      "context_window_tokens": 128000,
      "reserve_output_tokens": 4096,
      "compact_trigger_ratio": 0.85,
      "compact_summary_max_tokens": 2048,
      "model_context_windows": {
        "glm-4.7": 128000
      }
    }
  ]
}
```

建议默认值：

- `context_window_tokens = 32000`
- `reserve_output_tokens = 4096`
- `compact_trigger_ratio = 0.85`
- `compact_summary_max_tokens = 2048`

系统内还需要补一份“模型上下文长度默认表”，用于在配置未显式填写时提供默认值。

建议形式：

```python
DEFAULT_MODEL_CONTEXT_WINDOWS = {
    "glm-4.7": 128000,
    "qwen-plus": 131072,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
}
```

说明：

- 这是一份系统内置默认表
- 只用于“配置未显式覆盖”时的兜底
- 第一版只需要维护当前系统里常用、已验证过的模型
- 若模型不在该表中，则继续回退到 `context_window_tokens = 32000`

解析规则：

- `resolved_model = agent.model or llm_service.model`
- `resolved_context_window = model_context_windows.get(resolved_model)`
- 若上一步为空，则读取 `DEFAULT_MODEL_CONTEXT_WINDOWS.get(resolved_model)`
- 若仍为空，则回退到 `context_window_tokens`
- `hard_limit_tokens = resolved_context_window - reserve_output_tokens`
- `compact_trigger_tokens = floor(hard_limit_tokens * compact_trigger_ratio)`

说明：

- `model_context_windows` 优先级最高，用于配置文件显式覆盖
- `DEFAULT_MODEL_CONTEXT_WINDOWS` 是系统内置默认表
- `context_window_tokens` 是最后一层兜底值

---

## 4. usage 与估算

### 4.1 请求前估算

在 `_infer()` 开始时，对本次将发送给模型的完整输入做估算。

估算对象应包含：

- `system_prompt`
- `history messages`
- `tools schema`

建议复用当前项目依赖中的 `litellm.token_counter(...)`。

### 4.2 请求后实测

请求成功后，从底层响应提取 usage：

- `prompt_tokens`
- `completion_tokens`
- `total_tokens`

`tests/mock_llm_server.py` 当前已经有 usage 响应样例，可直接复用。

### 4.3 为什么仅靠 usage 不够

因为 usage 只有在请求成功后才能拿到。

如果请求直接因为上下文超长失败，就拿不到 usage。

因此必须同时保留：

- pre_check estimate
- overflow error 捕获

---

## 5. Compact 的触发条件

本次方案定义 3 类触发条件。

### 5.1 请求前估算超阈值

若：

- `estimated_prompt_tokens >= compact_trigger_tokens`

则：

- 在真正调用 `llmService.infer()` 之前先执行 compact
- compact 后重新构造消息列表
- 再发起请求

### 5.2 请求后实测接近上限

若：

- `usage.prompt_tokens >= compact_trigger_tokens`

则：

- 记录 usage
- 若本次 assistant 返回了 `tool_calls`，则跳过工具执行，直接先 compact，再重新 infer
- 若本次 assistant 没有 `tool_calls`，则只记录 usage，不额外触发 compact

这样处理的原因是：

- 当前 assistant 的工具调用尚未真正执行
- 此时直接 compact，不会产生 tool_call / tool_result 不配对问题
- compact 之后，这条未执行的 assistant tool_call 消息不再进入后续上下文视图

### 5.3 请求失败且识别为上下文超长

若底层返回错误命中以下任一特征：

- `context_length_exceeded`
- `maximum context length`
- `prompt is too long`
- `input is too long`
- `exceeds the context window`
- `too many tokens`

则：

- 触发 compact
- 自动重试一次

限制：

- 只重试一次，避免死循环

---

## 6. Compact 的表达方式

本次倾向不删除历史，不改写旧历史，只在 history 中追加 compact 结果。

### 6.1 记录方式

新增一条 history message：

- role: `user`
- tags: `[AgentHistoryTag.COMPACT_CMD]`
- content: compact 生成的摘要 prompt

明确约定：

- `COMPACT_CMD` 打在 compact 后写入 history 的这条 `user` 消息上
- 这条消息后续会作为输入再次发给模型
- `COMPACT_CMD` 不打在模型返回的 assistant 压缩结果上

当前约定使用 `user` role。

原因：

- 它更接近“把此前长历史整理成一段新的输入上下文，再继续驱动 agent 行动”
- 不会和系统提示词职责混在一起
- 与当前 history 的输入流形态更一致，接入成本更低

### 6.2 发给模型的消息构造

不能再直接把全部 `agent_histories` 原样导出。

需要在 `AgentHistoryStore` 中新增方法，例如：

- `build_infer_messages()`
- `find_latest_compact_index()`
- `estimate_tokens(...)`
- `should_compact(...)`

构造规则建议为：

1. 找到最新一条 `COMPACT_CMD`
2. 若不存在，则按当前全量 history 构造
3. 若存在，则：
   - 丢弃该条 compact 之前的原始消息
   - 从该条 compact 开始，拼接它之后的所有原始消息

效果等价于：

- “compact 之前的历史已经被摘要消息替代”

这样可以避免修改已有历史表结构与旧数据。

### 6.3 Compact 后的内存裁剪

compact 成功后，不仅“发给模型的消息视图”要切换，`AgentHistoryStore` 的内存列表也要同步裁剪。

规则：

1. compact 结果写入 history，形成一条带 `COMPACT_CMD` tag 的 `user` 消息
2. 立刻删除这条消息之前的全部内存 history item
3. 内存中只保留：
   - 最新 `COMPACT_CMD`
   - 以及它之后的消息

效果：

- 后续 `_infer()` 直接使用裁剪后的内存 history
- 不再需要每次从完整内存历史中重复做“取 latest compact 之后消息”的过滤
- 工具执行、turn 续跑、后续 compact 都建立在新的裁剪后内存态上

---

## 7. Compact 范围规则

compact 时，范围规则进一步简化。

当前规则：

1. 发起 compact 时，当前最后一条消息为 compact 命令
2. 真正参与压缩的内容，是这条 compact 命令之前的全部历史消息
3. compact 命令本身不作为被压缩内容参与摘要，而是作为一次新的输入指令存在

等价理解：

- “先追加一条 compact 命令，再把该命令之前的全部历史整理成一条 compact 输入”

这个规则不再区分：

- 是否属于未完成 turn
- 是否保留最近若干完整 turn

实现上只需要保证：

- compact 生成时，输入历史范围为 `history[:-1]`，其中 `history[-1]` 就是 compact 命令
- compact 完成后，下一次 infer 的消息视图为：
  - `latest_compact_message`
  - 以及 compact 之后新增的后续消息

---

## 8. Compact Prompt 结构

compact 不是简单截断，而是生成一条结构化摘要。

建议 prompt 模板包括：

- 角色长期目标与身份约束
- 已知事实
- 已做决策
- 尚未完成事项
- 关键工具调用结果
- 对后续行动仍有影响的上下文

建议输出约束：

- `compact_summary_max_tokens` 控制摘要长度
- 摘要必须可继续驱动后续推理，而不是仅做自然语言总结

一个可行的 compact 指令模板：

```text
请把以下对话历史压缩为“可继续执行任务的运行摘要”。

要求：
- 保留对当前任务仍然有用的事实、约束、决定、未完成事项
- 保留与工具调用结果相关的关键信息
- 删除寒暄、重复表达和已失效上下文
- 输出要简洁、结构化，便于后续继续推理
```

---

## 9. `_infer()` 链路建议

### 9.1 正常路径

`AgentTurnRunner._infer()` 内部建议调整为：

1. 从 `history` 构造本次 infer messages
2. 估算 token
3. 若超 pre_check 阈值，则先 compact，再重新构造 messages
4. 若 compact 后再次估算仍超长，则直接失败
5. 调用 `llmService.infer()`
6. 成功后记录 usage
7. 若 assistant 返回 `tool_calls` 且 post_check 命中超长风险，则跳过工具执行，直接 compact
8. compact 后重新构造 messages，再次 `_infer()`
9. 若 compact 后再次估算仍超长，则直接失败
10. 若未命中上述条件，则正常写入本轮 assistant history 并继续后续工具调用

### 9.2 错误恢复路径

若 `llmService.infer()` 失败且命中 context overflow：

1. 若本轮尚未 compact 过，则执行 compact
2. 重新构造 messages
3. 自动 retry once
4. 若 retry 仍失败，则按正常失败处理

---

## 10. 历史存储是否需要新增字段

这里不再保留两套方案，直接采用给 `agent_histories` 新增字段的方案。

建议新增列：

- `usage_json`

用途：

- 存储本次 history 对应的 token 相关信息
- 主要用于 `INFER` 阶段消息
- 允许 `INPUT` / `TOOL_RESULT` 阶段为空

### 10.1 存储内容

`usage_json` 建议存储一个 JSON 对象，字段如下：

```json
{
  "estimated_prompt_tokens": 118320,
  "prompt_tokens": 117842,
  "completion_tokens": 913,
  "total_tokens": 118755,
  "pre_check_triggered": true,
  "post_check_triggered": true,
  "overflow_retry": false
}
```

字段说明：

- `estimated_prompt_tokens`
  - 请求发出前估算的输入 token
- `prompt_tokens`
  - provider 返回的实际输入 token
- `completion_tokens`
  - provider 返回的实际输出 token
- `total_tokens`
  - provider 返回的总 token
- `pre_check_triggered`
  - 本轮在请求前是否命中过 compact 触发条件
- `post_check_triggered`
  - 本轮在请求成功后是否因 usage 接近上限而标记“下次优先 compact”
- `overflow_retry`
  - 本轮是否因为 context overflow 错误而触发过自动 compact 重试

### 10.2 空值规则

由于并不是每次都能拿到全部字段，因此允许部分字段为 `null`。

典型情况：

- 估算成功但 provider 没返回 usage：
  - `estimated_prompt_tokens` 有值
  - `prompt_tokens/completion_tokens/total_tokens` 为 `null`
- 请求在发送后直接 context overflow 失败：
  - `estimated_prompt_tokens` 有值
  - `overflow_retry=true`
  - provider usage 字段可能全部为 `null`
- 非 `INFER` 类型 history：
  - `usage_json` 可直接为 `null`

### 10.3 落库时机

建议：

- `append_stage_init(stage=INFER)` 时先插入 `usage_json = null`
- `finalize_history_item(...)` 成功或失败时补写完整 `usage_json`

这样可以兼容：

- 正常成功
- context overflow 后重试成功
- context overflow 后重试失败
- 其他异常失败

### 10.4 启动恢复规则

数据库中仍然保留完整历史，不物理删除 compact 之前的消息。

但在系统启动恢复 `AgentHistoryStore` 内存态时，需要按 compact 规则裁剪加载范围。

规则：

1. 从数据库加载某个 agent 的全部历史
2. 查找最新一条带 `COMPACT_CMD` tag 的消息
3. 若不存在 `COMPACT_CMD`，则按原样全部加载到 `AgentHistoryStore`
4. 若存在 `COMPACT_CMD`，则忽略该消息之前的所有历史
5. 仅把“最新 `COMPACT_CMD` 及其之后的消息”加载到 `AgentHistoryStore`

效果：

- 重启后内存态与运行中 compact 后的内存态保持一致
- 不会因为重启重新把 compact 之前的大量历史塞回内存
- 数据库里仍保留完整链路，便于审计和排障

---

## 11. 当前推荐决策

如果按“先做一版可用”的目标，推荐：

1. `COMPACT_CMD` 使用 `user` role
2. 第一版直接给 `agent_histories` 增加 `usage_json`
3. compact 复用当前模型
4. assistant 返回 `tool_calls` 且 post_check 命中时，跳过工具执行，直接 compact 后重新 infer
5. compact 后如果再次估算仍超长，直接失败，不继续 compact
6. overflow retry 最多一次

这样改动面最小，风险也最低。

---

## 12. 实际实现说明

本节记录实际落地与原设计的差异。

### 12.1 架构差异：compactPolicy 独立模块

原设计将 token 估算、阈值判断等逻辑放在 `AgentHistoryStore` 中。
实际实现将所有**决策/估算/判断**逻辑提取到独立的纯函数模块：

```
src/service/agentService/compactPolicy.py
```

该模块包含：

| 函数 | 作用 |
|------|------|
| `resolve_context_window(model, config)` | 按 model 查表 → 回退到 config |
| `calc_compact_trigger_tokens(model, config)` | `floor((context_window - reserve_output) * ratio)` |
| `estimate_tokens(model, messages, system_prompt)` | 调用 `litellm.token_counter()` |
| `should_trigger_pre_check(estimated, trigger)` | `estimated >= trigger` |
| `should_trigger_post_check(actual, trigger, has_tool_calls)` | `actual >= trigger and has_tool_calls` |
| `is_context_overflow_error(error)` | 关键词匹配 overflow 错误 |
| `should_fail_after_compact(estimated, trigger)` | compact 后仍超限 → True |
| `build_compact_prompt(messages, max_tokens)` | 构造压缩 prompt |
| `build_usage_payload(...)` | 构造 usage_json 字典 |

`AgentHistoryStore` 只保留纯数据操作（build_infer_messages、append_compact_message 等），不做决策。

### 12.2 模型上下文窗口查表

原设计在 config 中配置 `model_context_windows` 映射表。
实际实现在 `compactPolicy.py` 中硬编码 `DEFAULT_MODEL_CONTEXT_WINDOWS` 字典：

```python
DEFAULT_MODEL_CONTEXT_WINDOWS = {
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4-turbo": 128000,
    "gpt-3.5-turbo": 16385,
    "claude-3-5-sonnet": 200000,
    "claude-3-haiku": 200000,
    ...
}
```

查找逻辑：前缀匹配 → config.context_window_tokens 兜底（默认 32000）。

### 12.3 配置字段

`LlmServiceConfig` 新增 4 个字段（均有默认值）：

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `context_window_tokens` | 32000 | 兜底上下文窗口 |
| `reserve_output_tokens` | 4096 | 预留给输出的 token |
| `compact_trigger_ratio` | 0.85 | 触发压缩的比例 |
| `compact_summary_max_tokens` | 2048 | 压缩摘要最大 token |

### 12.4 _infer() 完整流程

```
build_infer_messages()
    │
    ├─ estimate_tokens()
    │
    ├─ Pre-check: estimated ≥ trigger?
    │   └─ YES → _execute_compact() → rebuild → re-estimate
    │       └─ 仍超限 → RuntimeError
    │
    ├─ llmService.infer()
    │   │
    │   ├─ 失败 + overflow + 未 pre-check?
    │   │   └─ YES → _execute_compact() → retry once
    │   │       └─ retry 后仍超限或再失败 → RuntimeError
    │   │
    │   └─ 失败 + 非 overflow
    │       └─ RuntimeError
    │
    └─ 成功
        ├─ Post-check: actual_prompt ≥ trigger AND has_tool_calls?
        │   └─ YES → _execute_compact() → re-infer（丢弃本次 assistant response）
        │       └─ re-infer 失败 → RuntimeError
        │
        └─ finalize_history_item(usage_json=...)
```

### 12.5 _execute_compact() 流程

1. `history.build_compact_source_messages()` → 获取源消息
2. `compactPolicy.build_compact_prompt()` → 构造压缩 prompt
3. `llmService.infer()` → 以 "对话历史压缩助手" 身份调用
4. `history.append_compact_message()` → 追加 COMPACT_CMD（user 角色）
5. `history.drop_messages_before_latest_compact()` → 内存裁剪

### 12.6 DB 变更

- `agent_histories` 新增 `usage_json TEXT` 列（migration `0020.sql`）
- `OpenAIResponse` 新增可选 `usage` 字段（`OpenAIUsage` 模型）
- `InferResult` 新增 `usage` 属性

### 12.7 启动恢复

`persistenceService.load_agent_history_message()` 在加载历史后调用 `_trim_to_latest_compact()`，
从最新 COMPACT_CMD 位置截取，使运行时视图与 compact 后一致。

### 12.8 测试覆盖

| 测试文件 | 覆盖内容 |
|----------|----------|
| `tests/unit/test_compact_policy.py` | compactPolicy 纯函数 20+ 用例 |
| `tests/unit/test_token_infrastructure.py` | 配置字段、OpenAIUsage、DB 字段 |
| `tests/unit/test_agent_history.py` | HistoryStore compact 方法 |
| `tests/unit/test_infer_compact.py` | _infer() 全路径 14 用例（pre/post/overflow/resume） |
