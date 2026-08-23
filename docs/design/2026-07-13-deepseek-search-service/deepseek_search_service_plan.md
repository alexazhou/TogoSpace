# DeepSeek 搜索服务集成计划

> 2026-07-13 | 计划中

---

## 一、背景

本次要增加的“搜索功能”不是在本地实现搜索引擎，也不是复用普通 LLM Provider 配置，而是通过 DeepSeek 的 LLM API 触发服务端搜索能力。

参考样例位于：

- `search_example/record-6500-request-2026-07-13-00-22-09.json`
- `search_example/record-6500-response-2026-07-13-00-22-44.json`

样例中的关键点：

- 请求使用 DeepSeek 的 Anthropic-style 接口形态。
- 固定模型为 `deepseek-v4-flash`。
- 通过内置工具 `web_search_20250305` / `web_search` 触发服务端搜索。
- `tool_choice` 强制选择 `web_search`。

---

## 二、目标

1. 在配置文件中新增三方服务集成配置，目前只支持 DeepSeek。
2. 在后台管理设置页左侧新增“三方服务集成”分组。
3. 支持 DeepSeek 搜索服务的编辑、保存和测试。
4. 在后端 `thirdPartyService` 中提供统一搜索入口：指定服务名称和 query 即可调用。

---

## 三、非目标

- 不把 DeepSeek 搜索服务混入现有 `llm_providers`。
- 不开放 `urls`、`models`、`options` 给用户配置。
- 不让用户选择搜索模型；模型由程序内置。
- 第一阶段不扩展多个搜索服务，只预留服务名称分发结构。

---

## 四、配置设计

在 `setting.json` 新增：

```json
{
  "third_party_services": {
    "deepseek": {
      "enabled": false,
      "api_key": ""
    }
  }
}
```

### 4.1 后端模型

在 `src/util/configTypes.py` 增加：

```python
class DeepSeekThirdPartyServiceConfig(BaseModel):
    enabled: bool = False
    api_key: str = ""


class ThirdPartyServicesConfig(BaseModel):
    deepseek: DeepSeekThirdPartyServiceConfig = Field(default_factory=DeepSeekThirdPartyServiceConfig)
```

并在 `SettingConfig` 中增加：

```python
third_party_services: ThirdPartyServicesConfig = Field(default_factory=ThirdPartyServicesConfig)
```

### 4.2 持久化

在 `configUtil._save_setting_to_file()` 中写回：

```python
raw["third_party_services"] = setting.third_party_services.model_dump(
    exclude_defaults=True,
    mode="json",
)
```

读取时依赖 Pydantic 默认值兼容旧配置。

---

## 五、后端服务结构

新增目录：

```text
src/service/thirdPartyService/
├── __init__.py
├── core.py
└── deepseekService.py
```

### 5.1 统一入口

统一入口放在 `thirdPartyService` 中：

```python
async def search(service_name: str, query: str) -> dict:
```

调用示例：

```python
from service import thirdPartyService

result = await thirdPartyService.search("deepseek", "小米 今天 新闻")
```

行为约定：

| 输入 | 行为 |
|------|------|
| `service_name="deepseek"` | 调用 `deepseekService.search(query)` |
| 未知服务名 | 返回 `success=False`，错误说明为不支持的三方服务 |
| 服务未启用 | 返回 `success=False`，提示 DeepSeek 搜索服务未启用 |
| API Key 为空 | 返回 `success=False`，提示未配置 API Key |
| `query` 为空 | 返回 `success=False`，提示 query 不能为空 |

### 5.2 DeepSeek 服务实现

DeepSeek 搜索实现文件固定命名为：

```text
src/service/thirdPartyService/deepseekService.py
```

内置常量：

```python
DEEPSEEK_SEARCH_URL = "https://api.deepseek.com/anthropic/v1/messages"
DEEPSEEK_SEARCH_MODEL = "deepseek-v4-flash"
DEEPSEEK_WEB_SEARCH_TOOL_TYPE = "web_search_20250305"
DEEPSEEK_WEB_SEARCH_TOOL_NAME = "web_search"
```

主函数：

```python
async def search(query: str) -> dict:
```

测试辅助函数：

```python
async def test_search(api_key: str, query: str) -> dict:
```

说明：

- `search()` 使用当前已保存配置。
- `test_search()` 使用前端当前编辑态 API Key，不要求先保存。
- 二者共用底层请求构造和响应解析逻辑。

---

## 六、DeepSeek 请求格式

请求体参考 `search_example`，由 `deepseekService.py` 内部构造：

```json
{
  "model": "deepseek-v4-flash",
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": "Perform a web search for the query: 小米 今天 新闻"
        }
      ]
    }
  ],
  "system": [
    {
      "type": "text",
      "text": "You are an assistant for performing a web search tool use"
    }
  ],
  "tools": [
    {
      "type": "web_search_20250305",
      "name": "web_search",
      "max_uses": 8
    }
  ],
  "tool_choice": {
    "type": "tool",
    "name": "web_search"
  },
  "max_tokens": 32000,
  "output_config": {
    "effort": "high"
  },
  "stream": false
}
```

### 6.1 不复用 `llmApiUtil.OpenAITool`

原因：

- 现有 `OpenAITool` 面向 OpenAI function calling schema。
- DeepSeek 搜索样例里的工具是服务端内置工具：`web_search_20250305`。
- 强行复用会导致模型结构不匹配，后续维护成本更高。

建议直接使用 `aiohttp` 发送 JSON 请求。

---

## 七、返回结构

`thirdPartyService.search()` 统一返回 dict：

```python
{
    "success": True,
    "service": "deepseek",
    "query": "小米 今天 新闻",
    "content": "...",
    "thinking": "...",
    "tool_use": [...],
    "usage": {...},
    "duration_ms": 1234,
}
```

失败返回：

```python
{
    "success": False,
    "service": "deepseek",
    "query": "小米 今天 新闻",
    "message": "错误说明",
    "error_type": "ValidationError",
}
```

接口层可直接将该结果返回给前端；Agent 工具层可将该 dict 序列化后交给模型。

---

## 八、后端接口

新增 controller 可放在 `src/controller/thirdPartyController.py`。

### 8.1 读取配置

```text
GET /config/third_party_services.json
```

返回：

```json
{
  "third_party_services": {
    "deepseek": {
      "enabled": false,
      "api_key": "",
      "has_api_key": false
    }
  }
}
```

demo mode 下隐藏 `api_key`。

### 8.2 保存配置

```text
POST /config/third_party_services.json
```

请求：

```json
{
  "third_party_services": {
    "deepseek": {
      "enabled": true,
      "api_key": "..."
    }
  }
}
```

### 8.3 测试 DeepSeek 搜索

```text
POST /config/third_party_services/deepseek/test.json
```

请求：

```json
{
  "enabled": true,
  "api_key": "...",
  "query": "小米 今天 新闻"
}
```

说明：

- 使用请求中的 API Key 和 query。
- 不要求先保存。
- 测试接口调用 `deepseekService.test_search(api_key, query)`。

---

## 九、前端改动

### 9.1 API 类型

在 `frontend/src/api.ts` 增加：

```ts
getThirdPartyServicesConfig()
saveThirdPartyServicesConfig(payload)
testDeepSeekSearchService(payload)
```

在 `frontend/src/types.ts` 增加：

```ts
export interface DeepSeekThirdPartyServiceConfig {
  enabled: boolean;
  api_key: string;
  has_api_key?: boolean;
}

export interface ThirdPartyServicesConfigPayload {
  third_party_services: {
    deepseek: DeepSeekThirdPartyServiceConfig;
  };
}
```

### 9.2 设置页导航

修改：

- `frontend/src/components/settings/settingsNavItems.ts`
- `frontend/src/components/settings/SettingsNavSidebar.vue`

新增分组：

```text
三方服务集成
└── DeepSeek 搜索
```

### 9.3 配置页面

新增：

```text
frontend/src/components/settings/ThirdPartyServicesSection.vue
```

字段：

- 启用开关
- API Key 输入框
- 测试 Query 输入框
- 测试按钮
- 保存按钮

测试按钮使用当前表单内容，不要求先保存。

---

## 十、Agent 工具接入

在后续实现中可新增工具：

```python
async def web_search(query: str, _context: ToolCallContext = None) -> dict:
    return await thirdPartyService.search("deepseek", query)
```

注册位置：

- `src/service/funcToolService/tools.py`
- `src/service/funcToolService/core.py`
- `src/service/agentService/toolRegistry.py`

工具类别：

```python
"web_search": ToolCategory.READ
```

第一版可以与配置页一起接入，这样功能不只停留在测试页，而是能被 Agent Function Calling 使用。

---

## 十一、测试计划

### 11.1 后端单测

- `ThirdPartyServicesConfig` 默认值加载。
- 旧 `setting.json` 无 `third_party_services` 时兼容启动。
- 保存三方服务配置后写回 `setting.json`。
- DeepSeek 请求 payload 符合 `search_example` 结构。
- `thirdPartyService.search("deepseek", query)` 可分发到 `deepseekService.search()`。
- 未知服务名、未启用、缺 API Key、空 query 返回结构化失败。

### 11.2 前端测试

- API 方法路径和 payload 正确。
- 设置页导航新增“三方服务集成”分组。
- DeepSeek 搜索配置页支持加载、编辑、保存、测试状态展示。

### 11.3 手动验证

使用真实 DeepSeek API Key：

1. 打开后台管理设置页。
2. 进入“三方服务集成 / DeepSeek 搜索”。
3. 填写 API Key。
4. 输入测试 query：`小米 今天 新闻`。
5. 点击测试，应返回搜索摘要、tool_use 和 usage。
6. 保存配置。
7. 通过 Agent 工具调用 `web_search` 验证真实搜索能力。

---

## 十二、文件变更清单

预计后端：

- `src/util/configTypes.py`
- `src/util/configUtil/core.py`
- `src/service/thirdPartyService/__init__.py`
- `src/service/thirdPartyService/core.py`
- `src/service/thirdPartyService/deepseekService.py`
- `src/controller/thirdPartyController.py`
- `src/route.py`
- 可选：`src/service/funcToolService/tools.py`
- 可选：`src/service/funcToolService/core.py`
- 可选：`src/service/agentService/toolRegistry.py`

预计前端：

- `frontend/src/api.ts`
- `frontend/src/types.ts`
- `frontend/src/pages/SettingsPage.vue`
- `frontend/src/components/settings/settingsNavItems.ts`
- `frontend/src/components/settings/SettingsNavSidebar.vue`
- `frontend/src/components/settings/ThirdPartyServicesSection.vue`
- `frontend/src/locales/zh-CN.json`
- `frontend/src/locales/en.json`

预计测试：

- `tests/unit/util/test_config_types.py`
- `tests/unit/service/test_third_party_service.py`
- `frontend/src/__tests__/api.test.ts`
- `frontend/src/composables/__tests__/useSettingsRouting.test.ts` 或对应设置页测试
