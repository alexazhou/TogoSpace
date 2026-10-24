# LLM Session Header

## 背景

上游网关需要区分「不同的对话上下文」，用于会话亲和、计费归集与日志串联。Agent 的 id 只能标识「谁在说话」，无法表达「说的是哪一段对话」——一次 compact 之后，上下文已经被摘要替换，语义上是一段新的对话。

因此引入按 agent session 生成的上游请求头：

```
X-Session-Id: <uuid>
```

## Session 划分规则

session 以 **compact 边界** 划分：

| 阶段 | session 首条消息 | 生命周期 |
|------|----------|------------------|
| 从零开始（未 compact） | 历史首条消息的行 id | 直到第一次 compact 成功 |
| 第 N 次 compact 之后 | 最新 `COMPACT_SUMMARY` 的行 id | 直到下一次 compact 成功 |

- compact **成功并插入摘要** 的瞬间，旧 session 结束；此后发出的请求（包括 overflow 重试）都使用新 session。
- compact 请求本身算在 **被压缩的旧 session** 上（摘要属于旧对话）。
- 视觉兜底识别请求算在 **发起识别的 agent 当前 session** 上。

`AgentHistoryStore.current_session_first_message_id` 就是 `_items[0].id`：compact 不变量保证 `_items[0]` 要么是对话首条消息，要么是 `COMPACT_SUMMARY`，两种情况下的首行 id 恰好就是当前 session 的首条消息。

## 生成方式

`util/sessionUtil.py`：

```python
_SESSION_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "togospace/agent-session")

def build_agent_session_id(agent_id: int, first_message_id: int) -> str:
    return str(uuid.uuid5(_SESSION_NAMESPACE, f"agent:{agent_id}:session:{first_message_id}"))
```

- **确定性**：同一 `(agent_id, first_message_id)` 在任何进程、任何重启后都得到同一个 UUID，进程重启不会打断网关侧的会话视图。
- **标准 UUIDv5**：满足严格的 header 格式校验。
- **不可反解**：无法从 UUID 逆推 agent id；排查时以日志中的 `agent_id` / `session_id` 对照。
- 命名空间值一旦发布不可修改，否则所有历史 session id 都会变化。

首条消息 id 为 `None`（空 history、该行尚未落库或 `agent_id` 为空）时 `session_id` 保持 `None`，不注入 header，而不是发送伪造值。

## 注入位置

业务层通过 `GtCoreAgentDialogContext.session_id` 表达本次请求所属 session；`llmService` 在 `_build_extra_headers()` 中把模型配置的 `extra_headers` 与 session 头合并后透传给 `litellm.acompletion(extra_headers=...)`。

当前发送方：

| 位置 | session 来源 |
|------|--------------|
| `AgentTurnRunner._infer_to_item` 主推理 | `self._session_id`（请求前现算） |
| `AgentTurnRunner._infer_to_item` overflow 重试 | 同上；此时 compact 已插入摘要，自动切到新 session |
| `compact.compact_messages` | 传入旧 session |
| `visionFallback.recognize` | 传入发起者当前 session |

`_session_id` 是 property，**每次请求前重新计算**，避免持有 compact 前的过期值。

## 优先级

系统 session 头 **高于** 模型配置的 `extra_headers`：用户在设置里手写 `X-Session-Id` 会被覆盖。agent 的会话身份是系统级事实，不是可调参数。

## 可观测性

- `LLM infer start` 日志新增 `session_id=%s`（无值时打印 `-`）。
- `LLM upstream request start` 日志本来就打印 sanitized headers，可直接看到 `X-Session-Id`。

## 与 Prompt Cache 的关系

两者都依赖「前缀稳定」，但相互独立：header 不参与缓存键，compact 切换 session 也不会清空 prompt cache。二者的边界恰好一致——一次 compact 既结束一个 session，也开启一个更短、更集中的新缓存前缀。
