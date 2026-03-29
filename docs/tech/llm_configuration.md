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
| `model` | 是 | 模型名称。建议遵循 LiteLLM 规范：`供应商/模型名`。 |
| `api_key` | 是 | 对应供应商的 API Key。 |
| `base_url` | 否 | API 端点地址。如果使用官方原生接口，部分供应商可省略。 |
| `enable` | 是 | 是否启用该服务。 |

---

## 3. 常见配置示例

### 3.1 使用 OpenAI 兼容接口 (如 DeepSeek, Qwen, OneAPI)
这是最通用的配置方式。系统会自动处理 URL 拼接和前缀识别。

```json
{
  "name": "deepseek-chat",
  "type": "openai-compatible",
  "model": "deepseek-chat",
  "api_key": "sk-your-key",
  "base_url": "https://api.deepseek.com",
  "enable": true
}
```
*提示：系统会自动识别并补全 `openai/` 前缀，同时会自动移除 URL 末尾冗余的 `/chat/completions`。*

### 3.2 直接使用 Anthropic (Claude)
直接调用 Anthropic 官方接口，无需 OpenAI 中转。

```json
{
  "name": "claude-sonnet",
  "type": "anthropic",
  "model": "anthropic/claude-3-5-sonnet-20240620",
  "api_key": "sk-ant-...",
  "base_url": "https://api.anthropic.com",
  "enable": true
}
```

### 3.3 使用 Google Gemini
```json
{
  "name": "gemini-pro",
  "type": "google",
  "model": "gemini/gemini-1.5-pro",
  "api_key": "your-google-api-key",
  "enable": true
}
```

### 3.4 使用本地模型 (如 Ollama)
```json
{
  "name": "ollama-qwen",
  "type": "openai-compatible",
  "model": "qwen2",
  "api_key": "not-needed",
  "base_url": "http://localhost:11434/v1",
  "enable": true
}
```

---

## 4. 进阶特性说明

### 4.1 自动模型前缀识别
为了简化配置，系统会根据 `type` 字段自动为 `model` 添加供应商前缀：
- **无需手动输入斜杠 `/` 前缀**：
    - 如果 `type` 为 `openai-compatible`，自动添加 `openai/`。
    - 如果 `type` 为 `anthropic`，自动添加 `anthropic/`。
    - 如果 `type` 为 `google`，自动添加 `gemini/`。
    - 如果 `type` 为 `deepseek`，自动添加 `deepseek/`。
- **示例**：如果 `type` 是 `anthropic`，`model` 写 `claude-3-5-sonnet`，代码会自动将其转换为 `anthropic/claude-3-5-sonnet`。
- **覆盖机制**：如果你在 `model` 中手动写了斜杠（如 `custom/my-model`），系统将尊重你的原始设置，不再自动添加前缀。

### 4.2 API 地址自动纠错
系统会自动清理 `base_url`：
- 自动移除末尾多余的斜杠 `/`。
- 自动移除末尾多余的 `/chat/completions` 路径。
- **推荐做法**：在配置文件中只写到 `/v1` 或域名根路径，例如 `https://api.openai.com/v1`。

### 4.3 切换默认模型
在 `setting.json` 的顶层修改 `default_llm_server`：
```json
{
  "setting": {
    "default_llm_server": "你的配置名称 (name)",
    "llm_services": [...]
  }
}
```

---

## 5. 故障排除
如果遇到 `BadRequestError` 或 `404`：
1. **检查 URL**：确保 `base_url` 没有重复包含 `/chat/completions`（虽然系统已处理，但建议规范化）。
2. **检查模型名**：部分供应商必须包含特定前缀，参考 [LiteLLM Providers 列表](https://docs.litellm.ai/docs/providers)。
3. **API Key**：检查 key 是否过期或权限不足。
