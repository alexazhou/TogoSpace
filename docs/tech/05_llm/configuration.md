# LLM 配置指南

本项目现已集成 [LiteLLM](https://github.com/BerriAI/litellm)，支持统一对接多种大模型供应商（如 OpenAI, Anthropic, Google Gemini, DeepSeek, 阿里云通义千问等）。

## 1. 配置文件路径
通常在 `config/setting.json` 中进行配置。

## 2. 配置项说明

在 `llm_services` 数组中，每一个服务包含以下字段：

| 字段 | 必填 | 说明 |
| :--- | :--- | :--- |
| `name` | 是 | 配置的唯一标识名，用于 `default_llm_server` 指定。 |
| `type` | 是 | 供应商类型。可选：`openai-compatible`, `anthropic`, `google`, `deepseek` 等。 |
| `model` | 是 | 模型名称。**由于系统支持自动补全前缀，此处只需填写模型主体名称。** |
| `api_key` | 是 | 对应供应商的 API Key。 |
| `base_url` | 否 | API 端点地址。如果使用官方原生接口，部分供应商可省略。 |
| `enable` | 是 | 是否启用该服务。 |
| `temperature` | 否 | 模型的输出温度（0.0 ~ 2.0），控制随机性。 |
| `extra_headers` | 否 | 字典类型，自定义的 HTTP 请求头。默认会注入 `{"User-Agent": "opencode"}`。 |
| `provider_params` | 否 | 字典类型，透传给底层提供商的其他参数。注意：不能覆盖系统级保留字段（如 `messages`, `tools`, `stream` 等）。 |
| `context_window_tokens` | 否 | 模型最大上下文 Token 数（默认 `131072`）。用于历史记录压缩计算。 |
| `reserve_output_tokens` | 否 | 为模型回复预留的输出 Token 数（默认 `16384`）。 |
| `compact_trigger_ratio` | 否 | 触发历史记录压缩的阈值比例（默认 `0.85`）。当当前 Token 数达到可用上下文的该比例时触发压缩。 |
| `compact_summary_max_tokens`| 否 | 压缩总结时，生成的摘要最大允许的 Token 数（默认 `6144`）。 |

---

## 3. 常见配置示例

### 3.1 使用 OpenAI 兼容接口 (如 DeepSeek, Qwen, OneAPI)
无需在 `model` 字段手动添加 `openai/` 前缀，系统已在底层映射。

```json
{
  "name": "qwen-plus",
  "type": "openai-compatible",
  "model": "qwen-plus",
  "api_key": "sk-your-key",
  "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "enable": true,
  "temperature": 0.7
}
```

### 3.2 直接使用 Anthropic (Claude)
无需手动添加 `anthropic/` 前缀，系统已在底层映射。

```json
{
  "name": "claude-sonnet",
  "type": "anthropic",
  "model": "claude-3-5-sonnet-20240620",
  "api_key": "sk-ant-...",
  "enable": true,
  "provider_params": {
    "max_tokens": 4096
  }
}
```

### 3.3 使用 Google Gemini
无需手动添加 `gemini/` 前缀，系统已在底层映射。

```json
{
  "name": "gemini-pro",
  "type": "google",
  "model": "gemini-1.5-pro",
  "api_key": "your-google-api-key",
  "enable": true
}
```

### 3.4 使用 DeepSeek 官方接口
无需手动添加 `deepseek/` 前缀，系统已在底层映射。

```json
{
  "name": "deepseek-chat",
  "type": "deepseek",
  "model": "deepseek-chat",
  "api_key": "sk-...",
  "enable": true
}
```

---

## 4. 进阶特性说明

### 4.1 自动模型路由映射
系统会根据 `type` 字段自动提取对应的服务商，并在底层调用 LiteLLM 时通过 `custom_llm_provider` 参数显式指定，因此你在 `model` 中**无需手动填写 `提供商/` 前缀**：

| `type` 配置值 | 底层映射的 provider | `model` 填写示例 |
| :--- | :--- | :--- |
| `openai-compatible` | `openai` | `gpt-4o`、`qwen-plus` |
| `anthropic` | `anthropic` | `claude-3-5-sonnet-20240620` |
| `google` | `gemini` | `gemini-1.5-pro` |
| `deepseek` | `deepseek` | `deepseek-chat` |

*注意：系统**不会**直接修改你填写的 `model` 字符串拼接前缀，而是通过 API 参数显式声明提供商，这避免了前缀解析混乱的问题。*

### 4.2 API 地址自动纠错
底层 `llmApiUtil` 会自动清理 `base_url`，防止请求路径出现重复：
- 自动移除末尾的 `/chat/completions` 或 `/chat/completions/`。
- 自动移除末尾多余的斜杠 `/`。
- **配置建议**：只需写到 API 的基准路径（如 `.../v1`）即可。

### 4.3 切换默认模型
在 `setting.json` 的顶层修改 `default_llm_server` 值为对应的 `name` 即可：
```json
{
  "setting": {
    "default_llm_server": "qwen-plus",
    "llm_services": [...]
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
2. **检查 URL 格式**：确保 `base_url` 是供应商要求的基准地址。
3. **API Key 有效性**：检查 Key 是否正确，以及是否具有调用该模型的权限。
4. **provider_params 冲突**：如果报错提示系统保留字段被覆盖，请检查 `provider_params` 中是否误填了受保护的属性（如 `messages`, `tools` 等）。
