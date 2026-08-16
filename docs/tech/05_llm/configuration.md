# LLM 配置指南

本项目现已集成 [LiteLLM](https://github.com/BerriAI/litellm)，支持统一对接多种大模型供应商（如 OpenAI, Anthropic, Google Gemini, DeepSeek, 阿里云通义千问等）。

## 1. 配置文件路径
通常在 `config/setting.json` 中进行配置。若文件不存在，系统会从
`assets/config_template.json`（v3 格式）复制一份作为默认模板。

配置格式为 **v3**（顶层 `version: "v3"`），由 `llm_providers` 结构承载模型配置。
旧版 `llm_services`（v1）/ `support_vision`（v2）配置会在加载时自动迁移，无需手动处理。

## 2. 配置项说明

### 2.1 提供商（`llm_providers`）

`llm_providers` 是数组，每个元素对应一个 API 服务提供商：

| 字段 | 必填 | 说明 |
| :--- | :--- | :--- |
| `name` | 是 | 提供商唯一标识名，如 `qwen`、`deepseek`（不可包含 `@`）。 |
| `type` | 是 | 提供商类型，如 `openai`、`aliyun`、`deepseek` 等（决定预置 URL）。 |
| `api_key` | 是 | 对应供应商的 API Key。 |
| `enable` | 是 | 是否启用该提供商。 |
| `urls` | 否 | 协议 → 端点地址映射，如 `{"openai": "https://..."}`。 |
| `models` | 是 | 该提供商下的模型列表（见 2.2）。 |

### 2.2 模型（`models[]`）

| 字段 | 必填 | 说明 |
| :--- | :--- | :--- |
| `name` | 是 | 模型名称。**系统会自动补全提供商前缀，只需填写模型主体名称。** |
| `protocol` | 是 | 协议类型，如 `openai`、`anthropic`。 |
| `enabled` | 否 | 是否启用该模型（默认 `true`）。 |
| `input` | 否 | **支持的输入类型列表**。缺省即纯文本 `["text"]`；支持读图的模型显式配 `["text", "image"]`。可选：`text` / `image` / `audio` / `video`。 |
| `temperature` | 否 | 模型的输出温度（0.0 ~ 2.0），控制随机性。 |
| `extra_params` | 否 | 字典类型，透传给底层提供商的其他参数。注意：不能覆盖系统级保留字段（如 `messages`, `tools`, `stream` 等）。 |
| `extra_headers` | 否 | 字典类型，自定义的 HTTP 请求头。 |
| `context_config` | 否 | 模型级上下文配置，未配置时使用顶层 `context_config`。含 `context_window_tokens`（默认 `131072`）、`reserve_output_tokens`（默认 `16384`）、`compact_trigger_ratio`（默认 `0.85`）、`compact_summary_max_tokens`（默认 `6144`）。 |

---

## 3. 常见配置示例

以下示例展示 `llm_providers` 数组中的单个提供商条目。无需在 `model` 字段手动添加
`提供商/` 前缀，系统已在底层映射。

### 3.1 OpenAI 兼容接口（文本模型，如 Qwen、DeepSeek、OneAPI）

```json
{
  "name": "qwen",
  "type": "aliyun",
  "api_key": "sk-your-key",
  "enable": true,
  "urls": {
    "openai": "https://dashscope.aliyuncs.com/compatible-mode/v1"
  },
  "models": [
    {
      "name": "qwen-plus",
      "protocol": "openai",
      "enabled": true,
      "temperature": 0.7
    }
  ]
}
```

### 3.2 视觉模型（支持读图，`input` 含 `image`）

```json
{
  "name": "qwen",
  "type": "aliyun",
  "api_key": "sk-your-key",
  "enable": true,
  "urls": {
    "openai": "https://dashscope.aliyuncs.com/compatible-mode/v1"
  },
  "models": [
    {
      "name": "qwen-vl-plus",
      "protocol": "openai",
      "enabled": true,
      "input": ["text", "image"]
    }
  ]
}
```

### 3.3 Anthropic (Claude)

```json
{
  "name": "claude",
  "type": "anthropic",
  "api_key": "sk-ant-...",
  "enable": true,
  "urls": {
    "anthropic": "https://api.anthropic.com"
  },
  "models": [
    {
      "name": "claude-3-5-sonnet-20240620",
      "protocol": "anthropic",
      "enabled": true,
      "input": ["text", "image"],
      "extra_params": {
        "max_tokens": 4096
      }
    }
  ]
}
```

---

## 4. 进阶特性说明

### 4.1 自动模型路由映射
系统会根据模型的 `protocol` 字段自动映射底层 LiteLLM provider，因此你在 `model` 中
**无需手动填写 `提供商/` 前缀**：

| `protocol` 配置值 | 底层映射的 provider | `model` 填写示例 |
| :--- | :--- | :--- |
| `openai` | `openai` | `gpt-4o`、`qwen-plus` |
| `anthropic` | `anthropic` | `claude-3-5-sonnet-20240620` |

*注意：系统**不会**直接修改你填写的 `model` 字符串拼接前缀，而是通过 API 参数显式声明提供商，这避免了前缀解析混乱的问题。*

### 4.2 API 地址自动纠错
底层 `llmApiUtil` 会自动清理 `urls` 中的端点地址，防止请求路径出现重复：
- 自动移除末尾的 `/chat/completions` 或 `/chat/completions/`。
- 自动移除末尾多余的斜杠 `/`。
- **配置建议**：只需写到 API 的基准路径（如 `.../v1`）即可。

### 4.3 切换默认模型
在 `setting.json` 的 `default_models` 中按槽位指定 `模型@提供商`：
```json
{
  "default_models": {
    "primary": "qwen-plus@qwen",
    "lite": "",
    "vision": "qwen-vl-plus@qwen",
    "advanced": ""
  }
}
```

### 4.4 Token 自动压缩与上下文管理
当对话极长时，系统会根据 Token 配置自动执行压缩策略（总结早期的对话记录）：
- **触发条件**：当当前请求的 Token 总量达到 `(context_window_tokens - reserve_output_tokens) * compact_trigger_ratio` 时。
- **配置建议**：除非你非常了解模型的真实上限，否则建议保留默认值，以防止超长对话导致 `ContextWindowExceededError`。

---

## 5. 故障排除
如果遇到 `BadRequestError` (400) 或 `Not Found` (404)：
1. **核对模型名称**：虽然系统会自动加前缀，但请确保模型主体名称（如 `glm-4`）是该供应商支持的。
2. **检查 URL 格式**：确保 `urls` 中的端点地址是供应商要求的基准地址。
3. **API Key 有效性**：检查 Key 是否正确，以及是否具有调用该模型的权限。
4. **extra_params 冲突**：如果报错提示系统保留字段被覆盖，请检查 `extra_params` 中是否误填了受保护的属性（如 `messages`, `tools` 等）。
