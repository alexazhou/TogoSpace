# LLM 请求预处理规则机制 (Request Rules)

本文档介绍了在真正向上游大模型供应商发送 API 请求前，系统在请求体上所执行的静态预处理与兼容性修复逻辑。相关代码位于 `src/service/llmService/llmRequestRules.py`。

## 1. 为什么需要请求规则？

不同的模型供应商针对 OpenAI 标准格式的兼容程度各有不同。如果在不加以处理的情况下直接传递标准对话历史，可能会导致服务端报错。例如，一些带有推理能力（Reasoning）的模型可能会对某些 `tool_choice` 参数格式或历史对话节点的属性表现出极度严苛的限制。

为此，系统引入了 `LlmRequestRule` 抽象类，通过“请求拦截器”的设计，能够在不污染核心对话组装逻辑的前提下，实现一系列的自动容错与字段修正。

## 2. 规则运行机制

在 `llmService.infer` 执行底层网络请求之前，所有的规则会被组装在 `_RULES` 元组中，并由 `apply_llm_request_rules` 函数链式调用。

1. **匹配检查**：针对每一条规则，首先调用其 `check_match(request)` 方法判断当前请求（例如包含了特定模型名称、或包含特定格式的历史消息）是否需要被修正。
2. **应用修改**：如果 `check_match` 返回 `True`，系统将调用 `apply(request)` 返回一个修复后的全新 `OpenAIRequest`（基于 `model_copy(update=...)`）。
3. **日志追踪**：系统会自动记录被触发的规则名称，并在 `llmService` 打印 Infer Request 的 Log 时将其体现在 `applied_rules` 参数中，便于后续的排障审计。

## 3. 如何添加一条新规则

如果未来集成了某个新的大模型供应商，发现它对 OpenAI 的请求体有特殊“洁癖”，可以通过增加一条规则来抹平差异，而不需要修改核心的 `build_request` 组装过程。

只需按照以下两步：

### 3.1 实现 LlmRequestRule 接口

在 `llmRequestRules.py` 中新建一个类继承 `LlmRequestRule`，并实现 `check_match` 与 `apply`：

```python
from util import llmApiUtil
from .llmRequestRules import LlmRequestRule

class RemoveUnsupportedParamRule(LlmRequestRule):
    """
    作用：当调用 xxx 模型时，移除它不支持的特定参数。
    """
    def check_match(self, request: llmApiUtil.OpenAIRequest) -> bool:
        # 仅当模型是特定模型，且存在需要被移除的参数时才拦截
        return "xxx-model" in request.model.lower() and request.tool_choice == "required"

    def apply(self, request: llmApiUtil.OpenAIRequest) -> llmApiUtil.OpenAIRequest:
        # 复制一个 request 副本并应用修改
        return request.model_copy(update={"tool_choice": None})
```

### 3.2 注册该规则

在 `llmRequestRules.py` 的底部，将你的新规则实例添加到 `_RULES` 元组中即可：

```python
_RULES: tuple[LlmRequestRule, ...] = (
    StripRequiredToolChoiceForReasoningRule(),
    FillMissingReasoningContentRule(),
    RepairToolArgumentsRule(),
    RemoveUnsupportedParamRule(), # <--- 在这里注册新规则
)
```
一旦注册完成，所有发往该特定供应商的请求都会自动获得容错修复。
