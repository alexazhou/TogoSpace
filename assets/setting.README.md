# setting.json 说明

`setting.json` 用于配置运行时参数，主要包括 LLM 服务、持久化路径和工作目录。

默认位置：

- `~/.agent_team/setting.json`
- 或启动时通过 `--config-dir` 指定目录

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

## `llm_services` 常用字段

- `name`：服务唯一标识
- `enable`：是否启用
- `base_url`：接口地址
- `api_key`：API Key
- `type`：常见值 `openai-compatible` / `anthropic` / `google` / `deepseek`
- `model`：模型名
- `context_window_tokens`：上下文窗口大小
- `reserve_output_tokens`：预留输出 token，默认 `8192`
- `compact_trigger_ratio`：触发 compact 的比例，默认 `0.85`
- `compact_summary_max_tokens`：compact 摘要 token 上限，默认 `2048`
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

- `setting.json` 只放运行配置，不放 team / role template 预置内容
- API Key 不要提交到 Git
- 删除 `setting.json` 后，下次启动会自动重新生成示例文件
