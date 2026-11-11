# History Token Accounting 重构方案（讨论稿）

> 状态：讨论中，未实施。本文只描述方案，不代表已落地代码。

## 1. 背景

当前 token 长度相关逻辑分散在三个层次：

- `compact.estimate_tokens()`：消息列表级估算，放在 compact 模块里；
- `contextUsage.calc_context_usage()`：既算分子（估算）又算分母、触发线；
- `AgentTurnRunner`：在 `_check_compact`、`_execute_compact`、活动记录、usage 落库之间手工传递多个 token 数字。

主要问题：

1. **估算函数位置不对**：`compact.py` 只应负责预算与压缩，却承担了通用 token 估算；调用方各自构建消息列表再 tokenize。
2. **数字与历史位置脱钩**：`usage.prompt_tokens` 挂在某条 assistant 历史行上，描述的是「这一行之前的输入」。现在它被当成独立数字传来传去，代码里没有「真值对应哪条消息」的概念。
3. **同一语义拆成多个参数**：`trigger_prompt_tokens`、`estimated_tokens`、`pre_compact_tokens`、`post_compact_tokens`、`used_tokens`，每个都有自己的「算过就别再算」约定。
4. **接口形状别扭**：`contextUsage.calc_context_usage(messages=[], system_prompt=..., used_tokens=...)`，只给真值时还要塞空的 `messages` 占位。
5. **compact 后的真值失效被掩盖**：compact 保留的尾部旧行可能带着压缩前的 `usage.prompt_tokens`。现在因为每次全量重算，这个问题没有暴露；改成「真值 + 增量」后必须显式处理。

## 2. 目标与非目标

### 目标

- **history 成为 token 长度的唯一口径**：全量估算、上游真值、当前上下文长度都从 `AgentHistoryStore` 取。
- **真值带位置**：能回答「这个 `prompt_tokens` 对应哪条消息」。
- **调用方去参数化**：`_check_compact` / `_execute_compact` / `_publish_context_usage` 不再接收 token 数字。
- **真值优先**：有可用上游 usage 时，当前上下文用「真值 + 尾部估算」，不再每次全量重估。
- **全量估算纳入 tools schema**：无真值锚点时，估算也不能漏掉请求实际携带的工具定义。
- **行为可解释**：压缩、上下文占用条、活动记录三处使用同一个快照口径。

### 非目标

- 不改流式 completion 的逐 delta 估算（输出还没进 history，仍归 `llmService` / `tokenUtil`）。
- 不改 compact 的裁剪规则、触发比例、模型上下文窗口配置。
- 不重新设计活动记录的前端展示结构。

## 3. 核心模型：`HistoryTokenSnapshot`

```python
@dataclass(frozen=True)
class HistoryTokenSnapshot:
    """History 当前 token 口径。"""

    #: 全量本地估算：system prompt + 全部未压缩消息（不含 pending 占位）。
    estimated_tokens: int

    #: 当前上下文长度口径：
    #: 有可用真值 = accurate_usage.prompt_tokens + 锚点行及其后消息的估算；
    #: 无可用真值 = estimated_tokens。
    current_tokens: int

    #: 最近一次上游返回的真实 usage，字段与上游 OpenAIUsage 对齐。
    #: 复用 util.llmApiUtil.OpenAIUsage，不另造同构类型。
    accurate_usage: llmApiUtil.OpenAIUsage | None = None

    #: 该 usage 对应的 history 行 seq（产出这次请求的 assistant 行）。
    #: 位置信息不属于上游 usage，放在嵌套对象外面。
    accurate_anchor_seq: int | None = None
```

### 3.1 嵌套对象为什么直接复用 `OpenAIUsage`

`util.llmApiUtil.OpenAIUsage` 已经是项目里上游 usage 的统一视图：

```python
class OpenAIUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    prompt_cache_usage: PromptCacheUsage | None = None
```

直接复用：

- 字段名天然对齐上游，上游新增字段只改一处；
- 活动记录、日志、history 都已有同一套字段名；
- 不需要维护第二套 `AccurateTokenUsage` 同构类型。

已知限制：`HistoryUsage` 目前只持久化了 `prompt_tokens` / `completion_tokens` / `total_tokens`，没有 `prompt_cache_usage`。因此从 history 还原出的 `accurate_usage.prompt_cache_usage` 会是 `None`（表示「历史没有记录」，不是「上游返回 0」）。这不影响 `current_tokens` 的计算。**已确认本次不扩展 `HistoryUsage`**，见 §8.3。

### 3.2 真值的语义

`accurate_usage.prompt_tokens` 来自锚点 assistant 行上的 `usage`：

```text
history:
  ... [USER u1] [ASSISTANT a1] [USER u2] [TOOL r1] [ASSISTANT a2]
                    ^ 锚点= a2 行（如果它带 usage）

accurate_usage.prompt_tokens = 发给上游的、包含 a2 之前全部输入的 prompt 长度
current_tokens = accurate_usage.prompt_tokens
               + estimate(a2 行自身的回复 + a2 之后新增的消息)
```

所以真值不是「当前长度」本身，而是「锚点之前的长度」。这一点用 `accurate_anchor_seq` 明确标出来。

## 4. `AgentHistoryStore` 新增接口

```python
class AgentHistoryStore:
    def token_snapshot(
        self,
        model: str,
        system_prompt: str | None = None,
        tools: list[llmApiUtil.OpenAITool] | None = None,
    ) -> HistoryTokenSnapshot:
        """返回当前 token 口径（带缓存）。"""

    def estimate_tokens(
        self,
        model: str,
        system_prompt: str | None = None,
        tools: list[llmApiUtil.OpenAITool] | None = None,
    ) -> int:
        """当前完整上下文的本地全量估算（便捷方法）。"""

    def invalidate_token_cache(self) -> None:
        """消息体被原地修改（如视觉兜底回填 recognition）后调用。"""
```

`tools` 只参与全量估算：`litellm.token_counter(..., tools=[...])` 已支持接收工具定义。传入前把 `OpenAITool` 转成请求形态的 dict（`model_dump`，`category` 字段已在模型里 `exclude=True`）。

同时把 `estimate_messages_tokens()` / `estimate_token_by_char()` 从 `compact.py` 搬到 history 这一层（或放到 history 专属的 helper 模块，由 history 统一调用）。`compact.py` 只保留：

- 上下文窗口解析、hard limit、trigger tokens；
- overflow 错误识别；
- `compact_messages()` 执行。

## 5. 计算规则

### 5.1 全量估算

```python
messages = history.build_infer_messages()   # 排除 pending infer 占位
estimated_tokens = estimate_messages_tokens(model, messages, system_prompt, tools)
```

`tools` 与真实请求保持一致，避免「新会话 / compact 后没有真值锚点」时估算偏低。

### 5.2 真值锚点

从 `_items` 尾部向前找满足以下条件的最后一行：

- `item.usage is not None`；
- `item.usage.prompt_tokens is not None`；
- 该行属于当前 session（见 §5.3）。

### 5.3 compact 后的锚点失效

`insert_compact_summary()` 会保留一段尾部消息，这些旧行上的 usage 描述的是压缩前的上下文，不能继续当锚点。

判据（已确认采用 summary id）：

1. 从 `_items` 尾部向前扫，找到第一条带 `COMPACT_SUMMARY` 的行（正常不变量下它就在 `_items[0]`，但实现不依赖这个位置假设）；
2. 取这行插入时获得的自增 `id` 作为 session 边界；
3. 锚点必须满足 `item.id > summary.id`。

为什么不能只看「在 summary 之后」：被 compact 保留下来的尾部旧行在列表顺序上也排在 summary 之后，但它们的 id 小于 summary，usage 是压缩前的，必须排除。

```text
_items = [COMPACT_SUMMARY(id=100), ASSISTANT(id=50, usage=压缩前), USER(id=51), ...]
                                        ^ 列表在 summary 之后，但 id=50 < 100，不能当锚点
```

不新增持久化字段。若后续出现 id 复用 / 迁移导致顺序语义不可靠，再考虑给 `HistoryUsage` 增加 `session_start_id`。

### 5.4 `current_tokens`

```python
if anchor is None:
    current_tokens = estimated_tokens
else:
    tail_messages = [msg for item in items[anchor_idx:] if item.message is not None]
    current_tokens = anchor.usage.prompt_tokens + estimate_messages_tokens(model, tail_messages)
```

注意 tail 从锚点行**自身**开始：真值只覆盖锚点之前的输入，锚点的 assistant 回复也要计入当前上下文。

## 6. 调用方改造

### 6.1 `AgentTurnRunner`

新增薄封装，便于测试：

```python
def _token_snapshot(self) -> HistoryTokenSnapshot:
    return self._history.token_snapshot(
        self.gt_agent.model,
        self.system_prompt,
        tools=self.tool_registry.export_openai_tools(),
    )
```

`_infer_to_item` 里实际发送的 `tools` 也来自同一个 registry（`_run_turn_loop` 开头导出后一路传递），因此 `_token_snapshot()` 重新导出即可得到同一份工具定义，不需要再作为参数穿过 `_check_compact` / `_execute_compact`。

`_infer_to_item`：

```python
snapshot, pre_compact_triggered = await self._check_compact("pre-check")
estimated_prompt_tokens = snapshot.current_tokens
```

`_check_compact`：

```python
async def _check_compact(self, check_stage: str) -> tuple[HistoryTokenSnapshot, bool]:
    snapshot = self._token_snapshot()
    if snapshot.current_tokens < trigger_tokens:
        return snapshot, False

    post_snapshot = await self._execute_compact(pre_snapshot=snapshot)
    if post_snapshot.current_tokens >= hard_limit_tokens:
        raise ...
    self._publish_context_usage(post_snapshot)
    return post_snapshot, True
```

`_execute_compact`：

```python
async def _execute_compact(
    self,
    *,
    pre_snapshot: HistoryTokenSnapshot | None = None,
) -> HistoryTokenSnapshot:
    ...
```

不再接收 `pre_compact_tokens`；压缩前后都从 history 取快照。

`_publish_context_usage`：

```python
def _publish_context_usage(self, snapshot: HistoryTokenSnapshot | None = None) -> None:
    snapshot = snapshot or self._token_snapshot()
    usage = contextUsage.calc_context_usage(
        model_name=self.gt_agent.model,
        used_tokens=snapshot.current_tokens,
    )
    ...
```

调用方不再判断「有 usage 用 usage、没有用估算」——assistant 行 finalize 后 history 里自然有最新真值锚点。

`snapshot_context_usage()`、`execute_manual_compact()` 走同一个 `_token_snapshot()`。

### 6.2 `contextUsage`

```python
def calc_context_usage(*, model_name: str, used_tokens: int) -> AgentContextUsage | None:
    ...
```

删除：

- `messages` / `system_prompt` 参数；
- `used_tokens: int | None` 的估算回退分支；
- `calc_context_usage_from_history()`。

只保留分子 / 分母 / 触发线组装，以及模型配置不可解析时返回 `None`。

### 6.3 `compact.py`

删除 `estimate_tokens` / `estimate_token_by_char`。不再被 contextUsage / turnRunner 当作估算入口。

### 6.4 `HistoryUsage` / 活动记录

- `HistoryUsage.estimated_prompt_tokens` 继续存「本次请求发出前的 prompt 估算」，但来源改成 `snapshot.current_tokens`。
- `HistoryUsage.prompt_tokens` / `completion_tokens` / `total_tokens` 仍是本次请求的上游真值。
- 活动 metadata 的 `estimated_prompt_tokens` 与 history 保持同源。

## 7. 缓存

一次推理链路里会多次读取同一口径：

- pre-check；
- 活动记录 metadata；
- 上下文占用广播；
- post-check；
- 可能还有 overflow retry。

建议 history 缓存最近一次快照：

```python
cache_key = (model, system_prompt, tools_fingerprint, self._revision)
```

`tools_fingerprint` 用工具定义的稳定序列化结果（如 `json.dumps([t.model_dump(mode="json") for t in tools])`）。工具列表通常很小，序列化成本远低于每次重新跑 tokenizer。

所有会改变消息体或 usage 的写操作递增 `_revision`：

- `append_history_message`
- `finalize_history_item`
- `insert_compact_summary`
- `replace`
- `reload_from_db`
- `invalidate_token_cache`（视觉兜底回填 recognition 等原地修改）

缓存只保留最后一份，不做多模型多 prompt 的 LRU；一次推理链路 model / system_prompt 稳定，足够。

## 8. 决策记录

### 8.1 `current_tokens` 是否包含最新 assistant 回复

**已确认：是。**

真值只覆盖锚点之前，锚点行自身的 assistant 回复及其后消息都要计入。后续影响：

- 上下文占用条会比现在略大；
- post-check 会比现在早一个 completion 的量触发。

这就是「当前上下文」的真实长度，接受该行为变化。

### 8.2 全量估算是否纳入 tools schema

**已确认：是。**

`estimate_messages_tokens(..., tools=...)` 把工具定义传给 `litellm.token_counter`。这样：

- 有真值锚点时：真值本身已包含 tools；
- 无锚点时（新会话 / compact 后）：全量估算也不再漏掉工具定义。

### 8.3 `HistoryUsage` 是否补存 `prompt_cache_usage`

先解释这个选项在问什么。

**上游 usage 的实际形状**（`util/llmApiUtil/OpenAiModels.py`）：

```json
{
  "prompt_tokens": 12000,
  "completion_tokens": 300,
  "total_tokens": 12300,
  "prompt_tokens_details": {
    "cached_tokens": 8000,
    "cache_creation_tokens": 0
  }
}
```

`OpenAIUsage` 会把它归一成：

```python
OpenAIUsage(
    prompt_tokens=12000,
    completion_tokens=300,
    total_tokens=12300,
    prompt_cache_usage=PromptCacheUsage(
        cached_tokens=8000,
        cache_write_tokens=0,
    ),
)
```

**history 现在存了什么**：assistant 历史行上的 `usage` 是 `HistoryUsage`，`_build_usage()` 只填了：

- `estimated_prompt_tokens`
- `prompt_tokens`
- `completion_tokens`
- `total_tokens`
- `compact_stage`
- `overflow_retry`

`prompt_cache_usage` 在落库时被丢掉了。

**所以**：`HistoryTokenSnapshot.accurate_usage` 如果直接复用 `OpenAIUsage`，从 history 还原时只能填前三个字段，`prompt_cache_usage` 永远是 `None`。这不会影响 `current_tokens`（token 长度只用到 `prompt_tokens`），但嵌套对象就不是上游 usage 的完整拷贝。

两个选择：

- **A（先不补）**：`accurate_usage.prompt_cache_usage` 恒为 `None`，表示「历史未记录」。重构范围小。
- **B（一起补）**：`HistoryUsage` 增加 `prompt_cache_usage: PromptCacheUsage | None`，`_build_usage()` 从 `inference_result.usage` 一起写入。旧数据反序列化默认 `None`，兼容。嵌套对象与上游完全一致，后续做 cache 命中率观测也不用再改数据结构。

**已确认：选 A。** 本次不扩展 `HistoryUsage`；`accurate_usage.prompt_cache_usage` 恒为 `None`。后续如果要做 cache 命中率观测，再单独补字段。

### 8.4 锚点边界

**已确认：用 summary id。**

实现上从 `_items` 尾部向上扫，找到第一条 `COMPACT_SUMMARY` 行，取其插入时获得的自增 `id` 作为边界；锚点必须 `item.id > summary.id`。

注意：不能只判断「在 summary 之后」。被 compact 保留的尾部旧行在列表顺序上也排在 summary 后面，但 id 小于 summary，usage 是压缩前的，必须排除（见 §5.3 的例子）。

## 9. 影响面

### 源码

- `src/service/agentService/agentHistoryStore.py`
- `src/service/agentService/compact.py`
- `src/service/agentService/contextUsage.py`
- `src/service/agentService/agentTurnRunner.py`
- `src/model/coreModel/agentModel.py`（`AgentContextUsage.used_tokens` 注释口径）

### 测试

- 大量用例当前 patch `agentTurnRunner.compact.estimate_tokens`，需改为 patch `AgentHistoryStore.token_snapshot` 或 runner 的 `_token_snapshot()`；
- `test_agent_context_usage.py` 中「没传 used_tokens 就回退估算」的用例迁到 history 单测；
- 集成测试中靠估算值触发 / 不触发 compact 的判据需要相应调整；
- 新增 history token 单测：
  - 无锚点 → `current_tokens == estimated_tokens`；
  - 有锚点 → 真值 + 尾部估算；
  - compact 保留旧行 → 旧 usage 不作为锚点；
  - 写操作后缓存失效；
  - pending infer 占位不计入。

### 文档

- `docs/tech/04_agent/token_compaction.md` §3 / §4 / §8；
- `docs/tech/01_architecture/architecture.md` 如有 HistoryUsage 字段说明则同步。

## 10. 实施顺序（待批准后执行）

1. `AgentHistoryStore` 增加 `HistoryTokenSnapshot`、`token_snapshot(model, system_prompt, tools)`、估算函数迁移、tools 纳入估算、缓存与失效。
2. `contextUsage` 收敛为 `calc_context_usage(model_name, used_tokens)`。
3. `compact.py` 删除估算函数。
4. `agentTurnRunner` 改用 `_token_snapshot()`，去掉 token 参数传递。
5. 更新单元 / 集成测试。
6. 同步技术文档。

## 11. 备选方案

### 方案 A：只暴露，不改口径

history 只提供 `estimated_tokens` + `accurate_usage` + `anchor_seq`，调用方自己决定用哪个。改动小，但 `_check_compact` 的参数仍然分散，达不到「简洁」的目标。

### 方案 B：完全增量化

所有上下文长度都从最近一次真值 + 增量累计，不做全量重估。性能最好，但对 compact、持久化恢复、system prompt 刷新、视觉回填等边界要求很高，容易产生难查的偏差。本方案保留全量估算作为兜底，是折中。

### 方案 C：把上游 usage 原样存进 history

`HistoryUsage` 直接嵌套 `OpenAIUsage`，而不是平铺 `prompt_tokens` 等字段。数据模型最干净，但涉及 DB 数据格式迁移与所有读取方改造，范围明显更大。
