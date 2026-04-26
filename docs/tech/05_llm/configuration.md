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

---

## 3. 常见配置示例

### 3.1 使用 OpenAI 兼容接口 (如 DeepSeek, Qwen, OneAPI)
系统会自动补全 `openai/` 前缀。

```json
{
  "name": "qwen-plus",
  "type": "openai-compatible",
  "model": "qwen-plus",
  "api_key": "sk-your-key",
  "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "enable": true
}
```

### 3.2 直接使用 Anthropic (Claude)
系统会自动补全 `anthropic/` 前缀。

```json
{
  "name": "claude-sonnet",
  "type": "anthropic",
  "model": "claude-3-5-sonnet-20240620",
  "api_key": "sk-ant-...",
  "enable": true
}
```

### 3.3 使用 Google Gemini
系统会自动补全 `gemini/` 前缀。

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
系统会自动补全 `deepseek/` 前缀。

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

### 4.1 自动模型前缀识别
系统会根据 `type` 字段自动为 `model` 添加 LiteLLM 所需的路由前缀，因此你在 `model` 中**无需手动填写斜杠 `/` 及其前面的部分**：

| `type` 配置值 | 自动补全的前缀 | 示例转换 |
| :--- | :--- | :--- |
| `openai-compatible` | `openai/` | `gpt-4o` -> `openai/gpt-4o` |
| `anthropic` | `anthropic/` | `claude-3` -> `anthropic/claude-3` |
| `google` | `gemini/` | `gemini-pro` -> `gemini/gemini-pro` |
| `deepseek` | `deepseek/` | `deepseek-chat` -> `deepseek/deepseek-chat` |

*注意：如果你在 `model` 中手动包含了斜杠（如 `my-custom/model-x`），系统将保留原样，不再自动补全。*

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

---

## 5. 故障排除
如果遇到 `BadRequestError` (400) 或 `Not Found` (404)：
1. **核对模型名称**：虽然系统会自动加前缀，但请确保模型主体名称（如 `glm-4`）是该供应商支持的。
2. **检查 URL 格式**：确保 `base_url` 是供应商要求的基准地址。
3. **API Key 有效性**：检查 Key 是否正确，以及是否具有调用该模型的权限。
