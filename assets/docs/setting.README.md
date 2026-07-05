# setting.json 说明

`setting.json` 是 TogoSpace 的运行时配置文件，用于配置 LLM 服务、持久化路径和工作目录等参数。

**注意**：修改配置文件后，需要重启 TogoSpace 应用才能生效。

**版本**：当前为 V2 格式。V1 格式（`llm_services`）会在启动时自动迁移到 V2。

默认位置：

- `~/.togo_agent/setting.json`（打包模式）
- `dev_storage_root/setting.json`（开发模式）

## 最小示例

```json
{
  "version": "v2",
  "llm_providers": [
    {
      "name": "deepseek",
      "type": "deepseek",
      "api_key": "YOUR_API_KEY_HERE",
      "models": [
        {
          "name": "deepseek-chat",
          "protocol": "openai"
        }
      ]
    }
  ],
  "default_models": {
    "primary": "deepseek-chat@deepseek"
  }
}
```

## 顶层字段

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `version` | string | `"v2"` | 配置版本，V1 格式会自动迁移 |
| `language` | string | `"zh-CN"` | 界面语言，可选 `zh-CN`（中文）、`en`（英文） |
| `development_mode` | bool | `false` | 前端开发模式开关，影响错误提示等交互行为 |
| `llm_providers` | list | `[]` | LLM 服务商列表 |
| `default_models` | object | `{}` | 默认模型槽位配置 |
| `context_config` | object | 见下方 | 全局上下文与压缩策略配置 |
| `default_room_max_rounds` | int | `100` | 房间默认最大轮次，`<= 0` 表示不限轮次 |
| `db_path` | string | 自动 | 数据库文件路径 |
| `workspace_root` | string | 自动 | 团队默认工作目录根路径 |
| `bind_host` | string | `"0.0.0.0"` | HTTP 服务监听地址 |
| `bind_port` | int | `8180` | HTTP 服务监听端口 |
| `auto_check_update` | bool | `true` | 启动时自动检查更新 |
| `demo_mode` | object | 见下方 | 演示模式配置 |
| `auth` | object | 见下方 | 鉴权配置 |

## `llm_providers` 服务商配置

每个服务商包含连接信息和模型列表。

```json
{
  "name": "deepseek",
  "type": "deepseek",
  "enable": true,
  "api_key": "sk-xxx",
  "urls": {
    "openai": "https://api.deepseek.com/v1/chat/completions"
  },
  "models": [
    {
      "name": "deepseek-chat",
      "protocol": "openai",
      "enabled": true
    }
  ]
}
```

### Provider 字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 是 | 服务商唯一标识 |
| `type` | string | 是 | 服务商类型，见下方枚举 |
| `api_key` | string | 是 | API Key |
| `enable` | bool | 否 | 是否启用，默认 `true` |
| `urls` | object | 否 | 协议对应的 URL，key 为协议类型 |
| `models` | list | 是 | 该服务商下的模型列表 |

### `type` 服务商类型枚举

| 值 | 说明 |
|----|------|
| `openai` | OpenAI |
| `anthropic` | Anthropic（Claude）官方 |
| `google` | 谷歌官方 |
| `deepseek` | DeepSeek |
| `aliyun` | 阿里云（通义千问） |
| `aliyun_coding` | 阿里云Coding Plan |
| `volcengine_coding` | 字节火山引擎 Coding Plan |
| `mimo` | 小米Mimo |
| `mimo_token_plan` | Mimo Token Plan |
| `opencode_go` | OpenCode Go |
| `other` | 其他自定义 |

### Model 字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 是 | 模型名称 |
| `protocol` | string | 是 | 协议类型：`openai` 或 `anthropic` |
| `enabled` | bool | 否 | 是否启用，默认 `true` |
| `support_vision` | bool | 否 | 是否支持视觉，默认 `false` |
| `temperature` | float | 否 | 温度参数 |
| `extra_params` | object | 否 | 模型级别的 litellm 参数 |
| `extra_headers` | object | 否 | 额外请求头 |
| `context_config` | object | 否 | 模型级别的上下文配置（覆盖全局） |

## `default_models` 默认模型槽位

配置各场景使用的默认模型，格式为 `模型名@服务商名`。

```json
{
  "primary": "deepseek-chat@deepseek",
  "lite": "",
  "vision": "",
  "advanced": ""
}
```

| 槽位 | 说明 |
|------|------|
| `primary` | 主模型，通用任务默认使用 |
| `lite` | 轻量模型，简单任务使用 |
| `vision` | 视觉模型，处理图片使用 |
| `advanced` | 高级模型，复杂任务使用 |

**校验规则**：保存配置时，后端会校验各槽位引用的 `模型名@服务商名` 是否存在于 `llm_providers` 中，不存在则报错。

## `context_config` 全局上下文配置

设置上下文窗口和压缩策略的全局默认值。单个模型可覆盖此配置。

```json
{
  "context_config": {
    "context_window_tokens": 131072,
    "reserve_output_tokens": 16384,
    "compact_trigger_ratio": 0.85,
    "compact_summary_max_tokens": 6144
  }
}
```

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `context_window_tokens` | int | `131072` | 上下文窗口大小 |
| `reserve_output_tokens` | int | `16384` | 预留输出 token 数 |
| `compact_trigger_ratio` | float | `0.85` | 触发 compact 的比例（0.0~1.0） |
| `compact_summary_max_tokens` | int | `6144` | compact 摘要 token 上限 |

## `extra_params` 配置

`extra_params` 是一个 JSON 对象，会直接合并到 litellm 的请求参数中。可用于配置模型特定的参数，如 `reasoning_effort`、`top_p` 等。

**禁止覆盖的系统字段**：

以下字段由系统自动管理，不能在 `extra_params` 中设置：

- `api_key`、`base_url`、`model`、`messages`
- `temperature`、`max_tokens`、`stream`
- `tools`、`tool_choice`
- `custom_llm_provider`、`cache_control_injection_points`

示例：

```json
{
  "extra_params": {
    "reasoning_effort": "high"
  }
}
```

## `demo_mode` 配置

演示模式配置，用于展示环境：

- `enabled`：是否启用演示模式，默认 `false`
- `freeze_data`：是否冻结数据（禁止增删改），默认 `true`
- `hide_sensitive`：是否隐藏敏感信息，默认 `true`

启用演示模式且 `freeze_data=true` 时，后端进入只读状态，所有写操作返回 403。

示例：

```json
{
  "demo_mode": {
    "enabled": true,
    "freeze_data": true,
    "hide_sensitive": true
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

## 本地监听地址与端口

默认监听地址是 `0.0.0.0`，默认端口是 `8180`。

如需手动指定端口，在 `setting.json` 顶层添加或修改 `bind_port`，例如：`"bind_port": 9000`。

如需同时指定监听地址，可一并设置 `bind_host`，例如：`"bind_host": "127.0.0.1"`。

---

# setting.json Description

`setting.json` is the runtime configuration file for TogoSpace, used to configure LLM services, persistence paths, working directories, and other parameters.

**Note**: After modifying the configuration file, you need to restart the TogoSpace application for the changes to take effect.

**Version**: Currently using V2 format. V1 format (`llm_services`) will be auto-migrated to V2 on startup.

Default Location:

- `~/.togo_agent/setting.json` (packaged mode)
- `dev_storage_root/setting.json` (development mode)

## Minimal Example

```json
{
  "version": "v2",
  "llm_providers": [
    {
      "name": "deepseek",
      "type": "deepseek",
      "api_key": "YOUR_API_KEY_HERE",
      "models": [
        {
          "name": "deepseek-chat",
          "protocol": "openai"
        }
      ]
    }
  ],
  "default_models": {
    "primary": "deepseek-chat@deepseek"
  }
}
```

## Top-level Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `version` | string | `"v2"` | Config version. V1 format auto-migrates. |
| `language` | string | `"zh-CN"` | UI language: `zh-CN` (Chinese), `en` (English). |
| `development_mode` | bool | `false` | Frontend dev mode switch. |
| `llm_providers` | list | `[]` | LLM provider list. |
| `default_models` | object | `{}` | Default model slot configuration. |
| `context_config` | object | see below | Global context & compaction config. |
| `default_room_max_rounds` | int | `100` | Default max rounds per room. `<= 0` = unlimited. |
| `db_path` | string | auto | Database file path. |
| `workspace_root` | string | auto | Default workspace root for teams. |
| `bind_host` | string | `"0.0.0.0"` | HTTP service bind host. |
| `bind_port` | int | `8180` | HTTP service bind port. |
| `auto_check_update` | bool | `true` | Auto-check updates on startup. |
| `demo_mode` | object | see below | Demo mode configuration. |
| `auth` | object | see below | Authentication configuration. |

## `llm_providers` Provider Configuration

Each provider contains connection info and a model list.

```json
{
  "name": "deepseek",
  "type": "deepseek",
  "enable": true,
  "api_key": "sk-xxx",
  "urls": {
    "openai": "https://api.deepseek.com/v1/chat/completions"
  },
  "models": [
    {
      "name": "deepseek-chat",
      "protocol": "openai",
      "enabled": true
    }
  ]
}
```

### Provider Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Unique provider identifier. |
| `type` | string | Yes | Provider type, see enum below. |
| `api_key` | string | Yes | API Key. |
| `enable` | bool | No | Whether enabled, default `true`. |
| `urls` | object | No | Protocol-to-URL mapping. |
| `models` | list | Yes | Model list for this provider. |

### `type` Provider Type Enum

| Value | Description |
|-------|-------------|
| `openai` | OpenAI |
| `anthropic` | Anthropic（Claude）官方 |
| `google` | 谷歌官方 |
| `deepseek` | DeepSeek |
| `aliyun` | Aliyun (Tongyi Qianwen) |
| `aliyun_coding` | 阿里云Coding Plan |
| `volcengine_coding` | 字节火山引擎 Coding Plan |
| `mimo` | 小米Mimo |
| `mimo_token_plan` | Mimo Token Plan |
| `opencode_go` | OpenCode Go |
| `other` | Other / Custom |

### Model Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Model name. |
| `protocol` | string | Yes | Protocol: `openai` or `anthropic`. |
| `enabled` | bool | No | Whether enabled, default `true`. |
| `support_vision` | bool | No | Vision support, default `false`. |
| `temperature` | float | No | Temperature parameter. |
| `extra_params` | object | No | Model-level litellm params. |
| `extra_headers` | object | No | Extra request headers. |
| `context_config` | object | No | Model-level context config (overrides global). |

## `default_models` Default Model Slots

Configure default models for different scenarios. Format: `model_name@provider_name`.

```json
{
  "primary": "deepseek-chat@deepseek",
  "lite": "",
  "vision": "",
  "advanced": ""
}
```

| Slot | Description |
|------|-------------|
| `primary` | Primary model, default for general tasks. |
| `lite` | Lightweight model for simple tasks. |
| `vision` | Vision model for image processing. |
| `advanced` | Advanced model for complex tasks. |

**Validation**: On save, the backend validates that each slot's `model@provider` reference exists in `llm_providers`. Non-existent references return an error.

## `context_config` Global Context Configuration

Set global defaults for context window and compaction strategy. Individual models can override this.

```json
{
  "context_config": {
    "context_window_tokens": 131072,
    "reserve_output_tokens": 16384,
    "compact_trigger_ratio": 0.85,
    "compact_summary_max_tokens": 6144
  }
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `context_window_tokens` | int | `131072` | Context window size. |
| `reserve_output_tokens` | int | `16384` | Reserved output tokens. |
| `compact_trigger_ratio` | float | `0.85` | Compaction trigger ratio (0.0~1.0). |
| `compact_summary_max_tokens` | int | `6144` | Max tokens for compaction summary. |

## `extra_params` Configuration

`extra_params` is a JSON object that merges directly into litellm request parameters. Use it for model-specific settings like `reasoning_effort`, `top_p`, etc.

**Prohibited System Fields**:

The following fields are managed by the system and cannot be set in `extra_params`:

- `api_key`, `base_url`, `model`, `messages`
- `temperature`, `max_tokens`, `stream`
- `tools`, `tool_choice`
- `custom_llm_provider`, `cache_control_injection_points`

Example:

```json
{
  "extra_params": {
    "reasoning_effort": "high"
  }
}
```

## `demo_mode` Configuration

Demo mode configuration for showcase environments:

- `enabled`: Whether demo mode is enabled, default `false`.
- `freeze_data`: Whether to freeze data (forbid add/edit/delete), default `true`.
- `hide_sensitive`: Whether to hide sensitive info, default `true`.

When demo mode is enabled and `freeze_data=true`, the backend enters read-only state, all write operations return 403.

Example:

```json
{
  "demo_mode": {
    "enabled": true,
    "freeze_data": true,
    "hide_sensitive": true
  }
}
```

## `auth` Configuration

API authentication configuration:

- `enabled`: Whether authentication is enabled, default `false`.
- `token`: Access token, must be set when auth is enabled.

When enabled, all HTTP API requests (except `/system/status.json`) require an `Authorization: Bearer <token>` header. WebSocket connections require `{type: "auth", token: "<token>"}` message for authentication.

Example:

```json
{
  "auth": {
    "enabled": true,
    "token": "your-access-token"
  }
}
```

## Local Bind Host and Port

Default bind host is `0.0.0.0`, default port is `8180`.

To specify a port, add `bind_port` at the top level: `"bind_port": 9000`.

To also specify a host: `"bind_host": "127.0.0.1"`.
