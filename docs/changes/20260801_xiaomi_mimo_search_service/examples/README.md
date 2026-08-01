# MiMo / DeepSeek 搜索接口样例

这些 JSON 由导出脚本实际请求接口后直接写入，用于对照完整请求和响应结构：MiMo 使用 query `今天260801天气怎么样`；DeepSeek 最近一次使用 query `今天2026年8月1日，天气预报`。

| 文件 | 说明 |
|---|---|
| `mimo_openai_search_request.json` | MiMo OpenAI 兼容格式搜索请求 |
| `mimo_openai_search_response.json` | MiMo 搜索响应，来源位于 `choices[0].message.annotations` |
| `deepseek_anthropic_search_request.json` | DeepSeek Anthropic-style 搜索请求 |
| `deepseek_anthropic_search_response.json` | DeepSeek 搜索响应，来源位于 `content[].content[]` 的 `web_search_result` |

注意：

- 所有 API Key 均已脱敏。
- 请求 JSON 直接对应实际发送的 request body，不包含 URL、请求方法或请求头。
- 响应 JSON 未做字段裁剪、摘要或手工改写；本次 DeepSeek 接口实际返回 10 条 `web_search_tool_result.content`，文件中按原始结果保留。
- API Key 未写入文档文件。
