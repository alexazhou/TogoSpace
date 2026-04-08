# LLM Prompt Cache

## 背景

同一 agent 连续行动时，请求前缀在语义上高度稳定（system prompt + compact summary + 历史轮次），但运行日志中的缓存指标持续为 0。

对比抓包数据：

- `testdata/request.json`：存在 `cache_control`
- `testdata/our_request.json`：不存在任何 `cache_control`

根因是请求中未声明缓存策略，而非消息结构问题（LiteLLM 发往 Anthropic 的请求已是原生 block 结构）。

## 实现

### 开关

业务层通过 `GtCoreAgentDialogContext.prompt_cache: bool`（默认 `True`）表达是否启用缓存。`llmService` 将其透传到 `OpenAIRequest.prompt_cache`。

### 缓存注入

`client.py` 在调用 `litellm.acompletion` 时，若 `request.prompt_cache=True`，附带：

```python
cache_control_injection_points = [
    {"location": "message", "role": "system"},   # system prompt 优先缓存
    {"location": "message", "index": -1},        # 最后一条消息作为第二边界
]
```

LiteLLM 负责把注入点转换为 Anthropic 的 `cache_control: ephemeral`；对不支持缓存的 provider 静默忽略，无需业务层区分 provider。

### 缓存指标归一化

不同 provider 返回的缓存字段不统一（`cache_read_input_tokens`、`prompt_tokens_details.cached_tokens` 等），`OpenAIUsage` 通过 `_normalize_prompt_cache_usage` 将其统一归一到 `prompt_cache_usage: PromptCacheUsage`：

```python
class PromptCacheUsage(BaseModel):
    cached_tokens: Optional[int]       # None = 上游未返回；0 = 上游明确返回 0
    cache_write_tokens: Optional[int]
```

归一规则：

- `cached_tokens`：优先取 `prompt_tokens_details.cached_tokens`，其次取 `cache_read_input_tokens`
- `cache_write_tokens`：优先取 `cache_creation_input_tokens`，其次取 `prompt_tokens_details.cache_creation_tokens`

### 可观测性

- 请求日志（`LLM upstream request start`）：`prompt_cache=True/False`
- 响应日志（`LLM upstream request success`）：完整 `usage` payload，含归一化后的 `prompt_cache_usage`

## 与 Compact 的关系

Prompt Cache 与 Compact 互补：

- Compact 负责限制上下文长度，维持稳定的摘要前缀
- Prompt Cache 复用这个稳定前缀，降低重算成本

`COMPACT_SUMMARY` 之前的内容是高价值缓存候选；compact 之后的稳定前缀更短、更集中，更有利于缓存命中。
