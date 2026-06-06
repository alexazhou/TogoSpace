# LLM 重试机制与错误分类

本文档介绍了 TeamAgent 底层在调用大模型 API 时的异常分类与重试策略。相关代码主要位于 `src/service/llmService/llmErrorClassifier.py` 与 `src/service/llmService/core.py`。

## 1. 错误分类 (Error Classification)

由于不同的模型供应商返回的错误代码和异常类型可能各不相同，系统底层通过统一的 `classify_llm_error` 函数将 `litellm` 抛出的具体异常抽象为统一的 `LlmErrorCategory` 枚举。

### 1.1 核心分类列表

| LlmErrorCategory | 对应异常/情况 | 是否重试 | 说明 |
| :--- | :--- | :--- | :--- |
| `CONTEXT_WINDOW` | `ContextWindowExceededError` 或文本匹配 | 否 | 上下文超长，超出模型支持的最大 Token 限制。 |
| `AUTH_ERROR` | `AuthenticationError`, `PermissionDeniedError` | 否 | 认证失败或 API Key 无权限访问该模型。 |
| `CONTENT_POLICY` | `ContentPolicyViolationError` | 否 | 触发服务商的安全或内容审核拦截。 |
| `INVALID_REQUEST`| `BadRequestError`, `InvalidRequestError` | 否 | 参数错误或非法的请求体（如不支持的特定参数组合）。 |
| `RATE_LIMITED` | `RateLimitError` | **是** | 触发频控或并发限制。 |
| `SERVER_ERROR` | `InternalServerError`, `ServiceUnavailableError` | **是** | 上游供应商服务端崩溃或服务不可用。 |
| `NETWORK_ERROR` | `APIConnectionError`, `Timeout` | **是** | 本地网络连接失败、DNS 问题或请求超时。 |
| `UNKNOWN` | 其他所有未分类的 Exception | **是** | 兜底的未知错误。 |

### 1.2 针对特定服务商的上下文超长处理

部分兼容 OpenAI 格式的服务商（如 DeepSeek 等），在遇到上下文超长时并不会返回标准错误码，而是统一抛出 400 `BadRequestError`。
为了精准识别，分类器内置了关键字探测逻辑：
如果异常类型为 `BadRequestError` 或 `InvalidRequestError`，并且异常的报错文本中包含了类似 `"maximum context length"`, `"context_length_exceeded"`, `"too many tokens"` 等关键字，系统会将其强制转换为 `CONTEXT_WINDOW` 错误，从而避免进入无效的重试死循环，并可以在上层触发历史记录自动压缩机制。

---

## 2. 自动重试策略 (Retry Logic)

针对标记为 **可重试 (Retryable)** 的错误（`RATE_LIMITED`, `SERVER_ERROR`, `NETWORK_ERROR`, `UNKNOWN`），底层 `_send_with_retry` 实现了带指数退避的自动重试策略。

### 2.1 重试参数

- **最大尝试次数**：8 次（1 次初始请求 + 7 次重试）。
- **重试间隔秒数**：`(2, 4, 8, 16, 32, 32, 32)`。

即第一次失败后等待 2 秒，第二次等待 4 秒，依此类推，最长单次等待被封顶在 32 秒。

### 2.2 重试状态广播

在重试的等待和执行期间，底层会通过回调抛出状态事件（`InferRequestStatusEvent`），事件包含：
- 当前重试的轮次（`attempt`）
- 等待的秒数（`retry_delay_seconds`）
- 具体的错误信息（`error_message`）

上层的 Agent 框架或 UI 监听该状态后，可以向前端广播（例如 `RETRY_SCHEDULED` 会在界面上展示“遇到错误正在重试，等待 x 秒...”），保证对用户侧的透明度和进度感知。
