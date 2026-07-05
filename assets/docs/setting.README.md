# setting.json 说明

`setting.json` 是 TogoSpace 的运行时配置文件，用于配置 LLM 服务、持久化路径和工作目录等参数。

**注意**：修改配置文件后，需要重启 TogoSpace 应用才能生效。

默认位置：

- `~/.togospace/setting.json`

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

- `language`：界面语言，默认 `zh-CN`，可选值：`zh-CN`（中文）、`en`（英文）
- `development_mode`：前端开发模式开关，默认 `false`。开启后前端会保留开发态交互，例如请求错误弹窗不自动消失
- `default_llm_server`：默认使用的服务名，必须等于某个 `llm_services[].name`
- `llm_services`：模型服务列表，至少要有一个 `enable=true`
- `default_room_max_rounds`：房间默认最大轮次，默认 `100`。`100` 表示群聊默认最多进行 100 轮后自动停止；`<= 0` 表示不限轮次
- `db_path`：数据库文件路径，默认为 `STORAGE_ROOT/data/data.db`
- `workspace_root`：团队默认工作目录根路径
- `bind_host`：后端 HTTP 服务监听地址，默认 `0.0.0.0`
- `bind_port`：后端 HTTP 服务监听端口，默认 `8180`
- `demo_mode`：演示模式配置，详见下方说明
- `auth`：鉴权配置，详见下方说明

## 本地监听地址与端口

默认监听地址是 `0.0.0.0`，默认端口是 `8180`。

如需手动指定端口，在 `setting.json` 顶层添加或修改 `bind_port`，例如：`"bind_port": 9000`。

如需同时指定监听地址，可一并设置 `bind_host`，例如：`"bind_host": "127.0.0.1"`。

## `development_mode` 配置

`development_mode` 用于控制前端是否启用开发态交互行为，由 `setting.json` 手动配置，不再根据运行环境自动推导。

- `false`：按正式环境交互处理，例如请求错误弹窗会在约 5 秒后自动消失
- `true`：启用开发态交互，例如请求错误弹窗需要手动关闭

示例：

```json
{
  "development_mode": true
}
```

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
- `temperature`：温度参数，可选
- `context_window_tokens`：上下文窗口大小，默认 `131072`
- `reserve_output_tokens`：预留输出 token，默认 `16384`
- `compact_trigger_ratio`：触发 compact 的比例，默认 `0.85`
- `compact_summary_max_tokens`：compact 摘要 token 上限，默认 `6144`
- `extra_headers`：额外请求头
- `provider_params`：透传给 litellm 的额外参数，详见下方说明

## `provider_params` 配置

`provider_params` 是一个 JSON 对象，会直接合并到 litellm 的请求参数中。可用于配置模型特定的参数，如 `reasoning_effort`、`top_p` 等。

**禁止覆盖的系统字段**：

以下字段由系统自动管理，不能在 `provider_params` 中设置：

- `api_key`、`base_url`、`model`、`messages`
- `temperature`、`max_tokens`、`stream`
- `tools`、`tool_choice`
- `extra_headers`、`custom_llm_provider`、`cache_control_injection_points`

示例：

```json
{
  "llm_services": [
    {
      "name": "deepseek",
      "provider_params": {
        "reasoning_effort": "high"
      }
    }
  ]
}
```

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

## `demo_mode` 配置

演示模式配置，用于展示环境：

- `enabled`：是否启用演示模式，默认 `false`
- `freeze_data`：是否冻结数据（禁止增删改），默认 `true`
- `hide_sensitive_info`：是否隐藏敏感信息，默认 `true`

启用演示模式且 `freeze_data=true` 时，后端进入只读状态，所有写操作返回 403。

示例：

```json
{
  "demo_mode": {
    "enabled": true,
    "freeze_data": true,
    "hide_sensitive_info": true
  }
}
```

## `auth` 配置

API 鉴权配置，用于保护后端接口：

- `enabled`：是否启用鉴权，默认 `false`
- `token`：访问令牌，启用鉴权时必须设置

启用鉴权后，所有 HTTP API 请求（除 `/system/status.json` 外）需携带 `Authorization: Bearer <token>` 请求头。WebSocket 连接后需发送 `{type: "auth", token: "<token>"}` 消息完成鉴权。

示例：

```json
{
  "auth": {
    "enabled": true,
    "token": "your-access-token"
  }
}
```

---

# setting.json Description

`setting.json` is the runtime configuration file for TogoSpace, used to configure LLM services, persistence paths, working directories, and other parameters.

**Note**: After modifying the configuration file, you need to restart the TogoSpace application for the changes to take effect.

Default Location:

- `~/.togospace/setting.json`

## Minimal Example

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

## Top-level Fields

- `language`: UI language, default `zh-CN`. Options: `zh-CN` (Chinese), `en` (English).
- `development_mode`: Frontend development mode switch, default `false`. When enabled, frontend retains development interactions (e.g., error popups don't auto-dismiss).
- `default_llm_server`: Default service name, must match one of `llm_services[].name`.
- `llm_services`: List of model services, at least one must have `enable=true`.
- `default_room_max_rounds`: Default max rounds for a room, default `100`. `100` means group chats stop after 100 rounds; `<= 0` means unlimited.
- `db_path`: Database file path, default `STORAGE_ROOT/data/data.db`.
- `workspace_root`: Default workspace root directory for teams.
- `bind_host`: Backend HTTP service bind host, default `0.0.0.0`.
- `bind_port`: Backend HTTP service bind port, default `8180`.
- `demo_mode`: Demo mode configuration, see details below.
- `auth`: Authentication configuration, see details below.

## Local Bind Host and Port

The default bind host is `0.0.0.0`, and the default port is `8180`.

To manually specify a port, add or modify `bind_port` at the top level of `setting.json`, e.g., `"bind_port": 9000`.

To also specify a bind host, set `bind_host` accordingly, e.g., `"bind_host": "127.0.0.1"`.

## `development_mode` Configuration

`development_mode` controls whether the frontend enables development interactive behaviors. It is manually configured in `setting.json` and no longer automatically inferred from the environment.

- `false`: Handled as production environment (e.g., error popups auto-dismiss after ~5 seconds).
- `true`: Enables development interactions (e.g., error popups require manual dismissal).

Example:

```json
{
  "development_mode": true
}
```

## Common Fields in `llm_services`

- `name`: Unique service identifier (used only to distinguish configs, not the model name; do not confuse with `model`).
- `enable`: Whether it is enabled.
- `base_url`: API endpoint URL.
- `api_key`: API Key.
- `type`: API format type, supports the following four:
  - `openai-compatible`: OpenAI compatible format.
  - `anthropic`: Anthropic native format (Claude).
  - `google`: Google Gemini format.
  - `deepseek`: DeepSeek native format.
- `model`: Model name.
- `temperature`: Temperature parameter, optional.
- `context_window_tokens`: Context window size, default `131072`.
- `reserve_output_tokens`: Reserved output tokens, default `16384`.
- `compact_trigger_ratio`: Ratio to trigger compact, default `0.85`.
- `compact_summary_max_tokens`: Max tokens for compact summary, default `6144`.
- `extra_headers`: Extra request headers.
- `provider_params`: Additional parameters passed to litellm, see details below.

## `provider_params` Configuration

`provider_params` is a JSON object that merges directly into litellm request parameters. It can be used to configure model-specific settings like `reasoning_effort`, `top_p`, etc.

**Prohibited System Fields**:

The following fields are automatically managed by the system and cannot be set in `provider_params`:

- `api_key`, `base_url`, `model`, `messages`
- `temperature`, `max_tokens`, `stream`
- `tools`, `tool_choice`
- `extra_headers`, `custom_llm_provider`, `cache_control_injection_points`

Example:

```json
{
  "llm_services": [
    {
      "name": "deepseek",
      "provider_params": {
        "reasoning_effort": "high"
      }
    }
  ]
}
```

## Local Service Example

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

## `demo_mode` Configuration

Demo mode configuration, for showcase environments:

- `enabled`: Whether demo mode is enabled, default `false`.
- `freeze_data`: Whether to freeze data (forbid add/edit/delete), default `true`.
- `hide_sensitive_info`: Whether to hide sensitive info, default `true`.

When demo mode is enabled and `freeze_data=true`, the backend enters a read-only state, and all write operations return 403.

Example:

```json
{
  "demo_mode": {
    "enabled": true,
    "freeze_data": true,
    "hide_sensitive_info": true
  }
}
```

## `auth` Configuration

API authentication configuration, used to protect backend endpoints:

- `enabled`: Whether authentication is enabled, default `false`.
- `token`: Access token, must be set when auth is enabled.

When enabled, all HTTP API requests (except `/system/status.json`) require an `Authorization: Bearer <token>` header. WebSocket connections require an `{type: "auth", token: "<token>"}` message to complete authentication.

Example:

```json
{
  "auth": {
    "enabled": true,
    "token": "your-access-token"
  }
}
```
