# setting.json 说明

`setting.json` 是 TogoAgent 的运行时配置文件，用于配置 LLM 服务、持久化路径和工作目录等参数。

**注意**：修改配置文件后，需要重启 TogoAgent 应用才能生效。

默认位置：

- `~/.togo_agent/setting.json`

## 最小示例

```json
{
  "default_llm_server": "qwen",
  "llm_services": [
    {
      "name": "qwen",
      "enable": true,
      "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
      "api_key": "YOUR_API_KEY_HERE",
      "type": "openai-compatible",
      "model": "qwen-plus"
    }
  ]
}
```

## 顶层字段

- `default_llm_server`：默认使用的服务名，必须等于某个 `llm_services[].name`
- `llm_services`：模型服务列表，至少要有一个 `enable=true`
- `default_room_max_turns`：房间默认最大轮次，默认 `100`
- `workspace_root`：团队默认工作目录根路径
- `bind_host`：后端 HTTP 服务监听地址，默认 `0.0.0.0`
- `bind_port`：后端 HTTP 服务监听端口，默认 `8080`

## 本地监听地址与端口

默认监听地址是 `0.0.0.0`，默认端口是 `8080`。

如需手动指定端口，在 `setting.json` 顶层添加或修改 `bind_port`，例如：`"bind_port": 9000`。

如需同时指定监听地址，可一并设置 `bind_host`，例如：`"bind_host": "127.0.0.1"`。

## `llm_services` 常用字段

- `name`：服务唯一标识（仅用于区分不同服务配置，不等于模型名称，不要与 `model` 字段混淆）
- `enable`：是否启用
- `base_url`：接口地址
- `api_key`：API Key
- `type`：API 格式类型，支持以下四种：
  - `openai-compatible`：OpenAI 兼容格式（适用于大部分国产模型服务商如阿里云、智谱、Moonshot 等）
  - `anthropic`：Anthropic 原生格式（适用于 Claude 模型）
  - `google`：Google Gemini 格式
  - `deepseek`：DeepSeek 原生格式
- `model`：模型名
- `context_window_tokens`：上下文窗口大小
- `reserve_output_tokens`：预留输出 token，默认 `8192`
- `compact_trigger_ratio`：触发 compact 的比例，默认 `0.85`
- `compact_summary_max_tokens`：compact 摘要 token 上限，默认 `6144`
- `extra_headers`：额外请求头

## 本地服务示例

```json
{
  "default_llm_server": "local",
  "llm_services": [
    {
      "name": "local",
      "enable": true,
      "base_url": "http://127.0.0.1:8787/llm/v1/messages",
      "api_key": "test-token",
      "type": "anthropic",
      "model": "glm-5",
      "context_window_tokens": 128000
    }
  ]
}
```

## 注意

- 删除 `setting.json` 后，下次启动会自动重新生成示例文件