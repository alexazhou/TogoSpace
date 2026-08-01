# Xiaomi MiMo 搜索服务集成开发计划

> 2026-08-01 | 计划中

---

## 一、背景

当前 TogoSpace 已通过 `thirdPartyService` 集成 DeepSeek 服务端搜索，并由 Agent 的 `web_search` 工具调用。

本次新增 Xiaomi MiMo 搜索服务，作为独立的三方服务配置接入：

- MiMo 的 API Key 单独保存于 `third_party_services.xiaomi_mimo`。
- 不复用、不读取 `llm_providers` 中的 MiMo 配置和 API Key。
- Agent 继续使用同一个逻辑工具 `web_search`，不暴露具体供应商细节。

此前已对 MiMo 接口做过实际请求验证：

| 接口格式 | 结果 |
|---|---|
| Anthropic Messages API + `web_search_20250305` | HTTP 200，但模型未获得搜索工具，未返回来源链接 |
| OpenAI Chat Completions API + `type: web_search` | HTTP 200，成功返回搜索摘要、来源标注和 `web_search_usage` |

因此本次实现只使用 MiMo 的 OpenAI 兼容接口。MiMo 官方联网搜索文档也明确以 OpenAI Chat Completions API 为示例，并说明联网搜索插件需要单独开通。[MiMo 联网搜索文档](https://mimo.mi.com/docs/zh-CN/usage-guide/tool-calling/web-search)

本次实际验证使用的请求和响应 JSON 参考样例见：[MiMo / DeepSeek 搜索接口样例目录](examples/)。

---

## 二、目标

1. 在三方服务配置中增加 Xiaomi MiMo 搜索服务。
2. 独立保存和管理 MiMo API Key。
3. 支持 MiMo 搜索服务的启用、配置和在线测试。
4. 让 `web_search` 在 DeepSeek 和 MiMo 之间按配置选择服务。
5. 原样发送 Agent 提供的 query，并将 DeepSeek / MiMo 返回的来源标准化后提供给 Agent 和前端。
6. 保持旧 DeepSeek 配置和旧 `setting.json` 的兼容性。

---

## 三、非目标

- 不把 MiMo 搜索混入现有 `llm_providers`。
- 第一阶段不接入 MiMo Anthropic 搜索。
- 第一阶段不实现流式搜索结果处理。
- 不让 Agent 工具增加 `provider` 参数；供应商由系统配置决定。
- 不将 MiMo 搜索实现复用为普通 LLM 对话请求。

---

## 四、配置设计

### 4.1 `setting.json`

新增配置建议如下：

```json
{
  "third_party_services": {
    "default_service": {
      "search": "deepseek"
    },
    "deepseek": {
      "enabled": false,
      "api_key": ""
    },
    "xiaomi_mimo": {
      "enabled": false,
      "api_key": ""
    }
  }
}
```

字段说明：

| 字段 | 说明 |
|---|---|
| `enabled` | 是否允许该服务被 Agent 搜索工具使用 |
| `api_key` | MiMo 独立 API Key，不与 `llm_providers` 共用 |
| `default_service.search` | `web_search` 未指定供应商时使用的搜索服务 |

MiMo 搜索使用的 URL 和模型由程序内置，不开放给用户配置。当前固定使用普通 MiMo API 的 `mimo-v2.5` 模型和 OpenAI 兼容接口。[MiMo API 接入说明](https://mimo.mi.com/docs/zh-CN/quick-start/summary/first-api-call)

### 4.2 后端模型

在 `src/util/configTypes.py` 增加：

```python
class XiaomiMiMoThirdPartyServiceConfig(BaseModel):
    """Xiaomi MiMo 三方搜索服务配置。"""

    enabled: bool = False
    api_key: str = ""


class DefaultServiceConfig(BaseModel):
    search: ThirdPartyServiceName = ThirdPartyServiceName.DEEPSEEK


class ThirdPartyServicesConfig(BaseModel):
    """三方服务集成配置。"""

    default_service: DefaultServiceConfig = Field(default_factory=DefaultServiceConfig)
    deepseek: DeepSeekThirdPartyServiceConfig = Field(
        default_factory=DeepSeekThirdPartyServiceConfig
    )
    xiaomi_mimo: XiaomiMiMoThirdPartyServiceConfig = Field(
        default_factory=XiaomiMiMoThirdPartyServiceConfig
    )
```

`SettingConfig` 和现有持久化逻辑无需新增特殊分支，继续使用：

```python
raw["third_party_services"] = setting.third_party_services.model_dump(
    exclude_defaults=True,
    mode="json",
)
```

旧配置没有 `xiaomi_mimo` 或 `default_service` 时，依赖 Pydantic 默认值加载，默认继续使用 DeepSeek。

### 4.3 配置校验

保存或调用前校验：

- 启用服务时必须配置 `api_key`。
- `default_service.search` 必须是已注册的搜索服务名称。
- MiMo 的 URL 和模型使用代码内置常量，不接受配置覆盖。
- 默认搜索服务未启用时，返回结构化 `ServiceDisabled`，不静默切换到另一个服务，避免用户无法判断实际使用了哪个供应商。

---

## 五、后端服务结构

新增文件：

```text
src/service/thirdPartyService/
├── __init__.py
├── core.py
├── deepseekService.py
└── xiaomiMimoService.py
```

### 5.1 服务枚举

在 `src/constants.py` 中增加：

```python
class ThirdPartyServiceName(EnhanceEnum):
    DEEPSEEK = "deepseek"
    XIAOMI_MIMO = "xiaomi_mimo"
```

### 5.2 MiMo 服务实现

新增 `src/service/thirdPartyService/xiaomiMimoService.py`，提供：

```python
async def search(query: str) -> dict:
    """使用已保存的 MiMo 配置执行搜索。"""


async def test_search(
    api_key: str,
    query: str,
) -> dict:
    """使用测试请求中的配置执行搜索，不要求先保存。"""
```

底层请求使用 `aiohttp`，并沿用 DeepSeek 服务中的 `certifi` SSL 处理，避免 macOS 系统证书链导致的 SSL 校验失败。

MiMo 服务内置常量：

```python
MIMO_SEARCH_URL = "https://api.xiaomimimo.com/v1/chat/completions"
MIMO_SEARCH_MODEL = "mimo-v2.5"
```

第一阶段不提供 Base URL 和 Model 的配置入口；后续如需支持 Token Plan 或切换模型，再单独扩展配置模型和校验逻辑。

### 5.3 统一入口

`src/service/thirdPartyService/core.py` 负责：

- 解析默认搜索服务。
- 根据 `ThirdPartyServiceName` 分发到 DeepSeek 或 MiMo。
- 统一处理空 query、服务未启用和未知服务。
- 不改写、不拆分、不解析 query，原样传给对应服务。

建议保留显式分发能力：

```python
async def search(
    service_name: ThirdPartyServiceName,
    query: str,
) -> dict[str, Any]:
```

另外增加：

```python
def get_default_search_service() -> ThirdPartyServiceName:
    """读取配置中的默认搜索服务。"""
```

### 5.4 Agent 工具调用

当前 `src/service/funcToolService/tools.py` 中的实现固定调用 DeepSeek：

```python
return await thirdPartyService.search(ThirdPartyServiceName.DEEPSEEK, query)
```

需要改为：

```python
service_name = thirdPartyService.get_default_search_service()
return await thirdPartyService.search(service_name, query)
```

`web_search` 的工具名称和 `ToolCategory.READ` 分类保持不变。`src/service/funcToolService/core.py` 的工具注册也保持不变。

---

## 六、MiMo OpenAI 请求格式

请求地址：

```text
https://api.xiaomimimo.com/v1/chat/completions
```

请求头：

```http
api-key: <MIMO_API_KEY>
Content-Type: application/json
```

请求体建议：

```json
{
  "model": "mimo-v2.5",
  "messages": [
    {
      "role": "user",
      "content": "小米 MiMo 今天有什么最新动态？"
    }
  ],
  "tools": [
    {
      "type": "web_search",
      "max_keyword": 3,
      "force_search": true,
      "limit": 5
    }
  ],
  "tool_choice": "auto",
  "max_completion_tokens": 1024,
  "stream": false,
  "thinking": {
    "type": "disabled"
  }
}
```

设计说明：

- `type: web_search` 是 MiMo 的服务端内置搜索工具，不是普通 Function Calling 工具。
- `force_search: true` 保证 Agent 的 `web_search` 工具确实触发联网搜索。
- `tool_choice` 使用 `auto`，搜索是否执行由内置搜索工具处理。
- `max_keyword` 第一阶段固定为 `3`，避免用户误配置导致搜索成本不可控。
- `limit` 第一阶段固定为 `5`，表示每个自动生成的搜索词最多返回 5 条结果；最多可能返回约 15 条页面来源。
- Base URL 和 Model 第一阶段由程序内置，不出现在 `setting.json` 和前端表单中。
- 第一阶段使用非流式请求，简化来源解析和测试页面展示。

---

## 七、来源链接处理

query 由 Agent 原样传入对应服务。TogoSpace 不向 query 添加来源提示，不做后缀解析、关键词拆分或内容改写。

DeepSeek 和 MiMo 的搜索 API 都会返回来源信息。TogoSpace 不根据 query 判断是否添加来源，而是根据两个 API 各自的响应结构自动识别来源并统一写入 `sources`。

### 7.1 MiMo 响应解析

MiMo 搜索结果位于：

```text
choices[0].message.annotations
```

其中 `type=url_citation` 的条目转换为统一来源结构：

```python
{
    "url": "https://example.com/article",
    "title": "来源标题",
    "summary": "来源摘要",
    "site_name": "站点名称",
    "publish_time": "2026-08-01T12:00:00+08:00",
    "logo_url": "https://example.com/favicon.ico",
}
```

`url`、`title`、`summary`、`site_name`、`publish_time`、`logo_url` 按原顺序逐条同名映射。

### 7.2 DeepSeek 响应解析

DeepSeek 搜索来源位于：

```text
content[].type == "web_search_tool_result"
content[].content[].type == "web_search_result"
```

每个 `web_search_result` 按原顺序逐条转换：`url` 映射到 `sources[].url`，`title` 映射到 `sources[].title`。`type` 只用于识别来源条目，`page_age` 和 `encrypted_content` 均忽略，不进入平台返回结构。

### 7.3 统一返回结构

以下平台方法和工具统一返回 `ThirdPartySearchResult`，不再区分内部服务结果、测试接口结果和 Agent 工具结果：

- `deepseekService.search()` / `deepseekService.test_search()`
- `xiaomiMimoService.search()` / `xiaomiMimoService.test_search()`
- `thirdPartyService.search()`
- DeepSeek / MiMo 搜索测试 HTTP API
- Agent 的 `web_search` 工具

工具执行框架会将该字典序列化为 JSON 字符串后交给 LLM。所有入口只返回统一字段，不额外暴露供应商的 `query`、`thinking`、`tool_use` 或 `usage`。

成功时：

```json
{
  "success": true,
  "service": "deepseek",
  "content": "搜索摘要或回答正文",
  "sources": [
    {
      "url": "https://example.com/weather",
      "title": "来源标题",
      "summary": "来源摘要",
      "site_name": "站点名称",
      "publish_time": "2026-08-01T12:00:00+08:00",
      "logo_url": "https://example.com/favicon.ico"
    }
  ],
  "duration_ms": 1234
}
```

字段约定：

| 字段 | 类型 | 说明 |
|---|---|---|
| `success` | `bool` | 是否搜索成功；成功为 `true` |
| `service` | `string` | 实际使用的服务，`deepseek` 或 `xiaomi_mimo` |
| `content` | `string` | 搜索摘要或模型生成的正文 |
| `sources` | `array` | 统一后的来源列表；没有可解析来源时返回空数组 |
| `duration_ms` | `integer` | HTTP 请求耗时，单位为毫秒 |

每个 `sources` 条目至少包含 `url`，其他字段根据供应商响应可选填充：`title`、`summary`、`site_name`、`publish_time`、`logo_url`。MiMo 的 `annotations` 和 DeepSeek 的 `web_search_result` 不直接暴露给调用方。

只要 MiMo 返回了 `annotations`，就逐条转换为 `sources`；只要 DeepSeek 返回了 `web_search_result`，也逐条转换为 `sources`。如果没有可解析来源，`sources` 返回空数组，不影响 `content`。

失败时：

```json
{
  "success": false,
  "service": "deepseek",
  "content": null,
  "sources": null,
  "duration_ms": 0,
  "error_message": "DeepSeek 搜索服务未启用",
}
```

失败结果也输出统一字段，调用方通过 `success` 和 `error_message` 判断失败原因。

---

## 八、后端接口

### 8.1 读取配置

现有接口继续使用：

```text
GET /config/third_party_services.json
```

返回示例：

```json
{
  "third_party_services": {
    "default_service": {
      "search": "deepseek"
    },
    "deepseek": {
      "enabled": false,
      "api_key": "sk-deepseek-..."
    },
    "xiaomi_mimo": {
      "enabled": true,
      "api_key": "sk-mimo-..."
    }
  }
}
```

约定：

- 正常模式下直接返回已保存的真实 API Key，前端加载完整配置后可原样提交，避免覆盖其他服务的 Key。
- demo mode 下继续将 API Key 强制返回为空字符串。
- MiMo 的 URL 和 Model 不作为配置字段返回。

### 8.2 保存配置

继续使用：

```text
POST /config/third_party_services.json
```

请求示例：

```json
{
  "third_party_services": {
    "default_service": {
      "search": "xiaomi_mimo"
    },
    "deepseek": {
      "enabled": false,
      "api_key": "sk-deepseek-..."
    },
    "xiaomi_mimo": {
      "enabled": true,
      "api_key": "sk-..."
    }
  }
}
```

前端保存时必须同时提交 DeepSeek 和 MiMo 配置，避免现有整体替换逻辑清除另一方配置。

### 8.3 测试 MiMo 搜索

新增：

```text
POST /config/third_party_services/xiaomi_mimo/test.json
```

请求：

```json
{
  "enabled": true,
  "api_key": "sk-...",
  "query": "小米 MiMo 最新动态"
}
```

测试请求直接使用请求体中的 API Key 和 Query；Base URL、Model 使用程序内置常量，不要求先保存配置。

### 8.4 错误响应

HTTP、网络和响应解析失败统一通过 `error_message` 返回；错误消息中禁止输出 API Key。

---

## 九、前端改动

### 9.1 类型和 API

修改 `frontend/src/types.ts`：

```ts
export type ThirdPartySearchService = 'deepseek' | 'xiaomi_mimo';

export interface XiaomiMiMoThirdPartyServiceConfig {
  enabled: boolean;
  api_key: string;
}

export interface ThirdPartySearchSource {
  url: string;
  title?: string;
  summary?: string;
  site_name?: string;
  publish_time?: string;
  logo_url?: string;
}

export interface ThirdPartySearchResult {
  success: boolean;
  service: ThirdPartySearchService;
  content?: string;
  sources?: ThirdPartySearchSource[];
  duration_ms?: number;
  error_message?: string;
}
```

扩展：

- `ThirdPartyServicesConfigPayload`
- `ThirdPartyServicesConfigPayload.third_party_services.default_service.search`
- `ThirdPartySearchResult`，与后端服务方法、测试 API 和 `web_search` 工具结构一致
- `ThirdPartySearchResult.sources`

修改 `frontend/src/api.ts`：

- `getThirdPartyServicesConfig()` 保持不变
- `saveThirdPartyServicesConfig()` 保持不变
- 增加 `testXiaomiMimoSearchService()`

### 9.2 三方服务设置页

当前 `ThirdPartyServicesSection.vue` 基本围绕 DeepSeek 硬编码，需要增加 MiMo 分支，或将服务卡片和详情表单改为数据驱动。

三方服务列表页增加“默认搜索服务”选择器，选项为 `DeepSeek` 和 `Xiaomi MiMo`。选择结果写入 `default_service.search`，并与两个服务的完整配置一起保存。

MiMo 配置页字段：

- 启用开关
- API Key
- 搜索测试 Query
- 测试按钮
- 保存按钮

页面固定展示当前内置搜索模型 `mimo-v2.5`，但不提供修改入口。

页面说明：

- MiMo 需要在控制台开通联网搜索插件。
- 联网搜索会产生额外的搜索调用和 Token 消耗。
- 第一阶段固定使用普通 MiMo API；Token Plan 暂不在本次范围内。

测试结果展示：

- 搜索摘要
- 搜索耗时
- 来源标题
- 来源网站
- 可点击来源 URL

### 9.3 路由和国际化

修改：

- `frontend/src/composables/useSettingsRouting.ts`
- `frontend/src/locales/zh-CN.json`
- `frontend/src/locales/en.json`

详情页路由参数使用：

```text
thirdPartyService=xiaomi_mimo
```

面包屑需要支持：

```text
设置 / 三方服务集成 / Xiaomi MiMo
```

### 9.4 构建产物

前端修改完成后重新构建，更新后端实际提供的静态资源目录：

```text
assets/frontend/
```

---

## 十、测试计划

### 10.1 后端单元测试

在 `tests/unit/service/test_third_party_service.py` 增加：

- `ThirdPartyServicesConfig` 默认值包含 MiMo 配置。
- 旧配置缺少 MiMo 字段时可以正常加载。
- MiMo API Key 与 `llm_providers` 完全独立。
- 配置保存后正确写回 `third_party_services.xiaomi_mimo`。
- MiMo 请求 URL、Header 和 payload 正确。
- MiMo 使用 `type: web_search`、`force_search: true`。
- MiMo 响应中的 `annotations` 正确转换为 `sources`。
- DeepSeek 响应中的 `web_search_result` 正确转换为 `sources`。
- DeepSeek 响应中的 `page_age` 和 `encrypted_content` 不进入平台返回结构。
- Agent 传入的 query 原样发送给 MiMo。
- DeepSeek / MiMo 返回来源时，自动转换为统一的 `sources` 字段，不依赖 query 内容。
- 服务方法、测试 API 和 `web_search` 返回完全一致的 `ThirdPartySearchResult`，且不包含 `query`、`thinking`、`tool_use`、`usage`。
- MiMo 未启用、API Key 缺失或固定请求参数无效时返回结构化错误。
- HTTP 401、429、5xx、超时和非法 JSON 的错误处理正确。
- 默认搜索服务为 MiMo 时，Agent 工具实际分发到 MiMo。
- 默认搜索服务为 DeepSeek 时，旧行为不回归。

### 10.2 Controller/API 测试

- GET 配置在正常模式下返回两个服务的真实 API Key。
- demo mode 下不返回 API Key。
- POST 配置时支持 DeepSeek 和 MiMo 同时提交。
- GET 后不修改 API Key 直接 POST，可以完整保留两个服务的 Key。
- MiMo 测试接口使用未保存的请求参数。
- 测试接口不会把 API Key 写入响应或日志。

### 10.3 前端测试

- MiMo 配置加载和保存 payload 正确。
- 默认搜索服务选择器可以加载、切换和保存 `default_service.search`。
- MiMo 测试 API 路径和请求体正确。
- MiMo 详情页可以打开、返回和显示面包屑。
- API Key 输入框支持显示/隐藏。
- 搜索结果来源 URL 可以点击。
- 保存 MiMo 配置不会清除 DeepSeek 配置。

### 10.4 手动验证

1. 在三方服务配置中填写 MiMo API Key。
2. 确认程序内置的 MiMo API 和 `mimo-v2.5` 可用。
3. 在 MiMo 控制台确认已开通联网搜索插件。
4. 保存并启用 Xiaomi MiMo。
5. 将默认搜索服务切换为 `xiaomi_mimo`。
6. 通过 Agent 调用：

   ```text
   小米 MiMo 今天有什么最新动态
   ```

7. 验证 Agent 能得到摘要和 `sources`。
8. 切回 DeepSeek，验证原有搜索行为仍然可用。

---

## 十一、实施顺序

### 阶段 1：后端模型和配置

- 增加 `XIAOMI_MIMO` 服务枚举。
- 增加 MiMo 独立配置模型。
- 更新配置模板；正常模式读取真实 API Key，demo mode 隐藏 API Key。
- 完成配置持久化测试。

### 阶段 2：MiMo 搜索服务

- 新增 `xiaomiMimoService.py`。
- 实现 OpenAI 请求 payload。
- 实现错误处理和响应解析。
- 实现 MiMo `annotations` 到 `sources` 的转换。
- 增加 MiMo 服务单测。

### 阶段 3：统一搜索和 Agent 工具

- 增加默认搜索服务解析。
- 实现 DeepSeek `web_search_result` 到 `sources` 的转换。
- 让 DeepSeek / MiMo 服务方法、测试 API 和 Agent 工具统一返回 `ThirdPartySearchResult`。
- 修改 `web_search` 去掉 DeepSeek 固定绑定。
- 保持工具名称和权限类别不变。

### 阶段 4：Controller 和前端

- 增加 MiMo 测试接口。
- 扩展配置读写 API 类型。
- 改造三方服务设置页。
- 增加中英文文案。
- 增加来源链接展示。

### 阶段 5：集成验证

- 运行后端 unit/integration 测试。
- 运行前端测试和构建。
- 使用真实 MiMo API Key 完成一次搜索。
- 验证 DeepSeek/MiMo 切换和旧配置兼容。

---

## 十二、文件变更清单

预计新增：

- `docs/changes/20260801_xiaomi_mimo_search_service/plan.md`
- `docs/changes/20260801_xiaomi_mimo_search_service/examples/`
- `src/service/thirdPartyService/xiaomiMimoService.py`
- `src/service/thirdPartyService/result.py`

预计修改后端：

- `src/constants.py`
- `src/util/configTypes.py`
- `src/controller/thirdPartyController.py`
- `src/route.py`
- `src/service/thirdPartyService/__init__.py`
- `src/service/thirdPartyService/core.py`
- `src/service/funcToolService/tools.py`
- `assets/config_template.json`

预计修改前端：

- `frontend/src/api.ts`
- `frontend/src/types.ts`
- `frontend/src/components/settings/ThirdPartyServicesSection.vue`
- `frontend/src/composables/useSettingsRouting.ts`
- `frontend/src/locales/zh-CN.json`
- `frontend/src/locales/en.json`
- `assets/frontend/`（构建产物）

预计修改测试：

- `tests/unit/service/test_third_party_service.py`
- `frontend/src/__tests__/api.test.ts`
- 前端设置页相关测试（如已有组件测试覆盖）

---

## 十三、验收标准

- MiMo API Key 只保存在 `third_party_services.xiaomi_mimo`。
- MiMo 搜索请求使用 OpenAI 兼容格式，不使用 Anthropic 搜索格式。
- MiMo 搜索能够返回摘要和来源标注。
- MiMo 返回来源标注时，结果包含标准化 `sources`。
- `web_search` 可以通过默认配置在 DeepSeek 和 MiMo 之间切换。
- DeepSeek 原有功能和配置不回归。
- 正常模式的配置读取接口返回真实 API Key；demo mode 配置响应隐藏 API Key。
- API Key 不出现在搜索结果、测试结果、日志和错误消息中。
- 旧配置文件无需手动迁移即可启动。
- 前端可以完成默认搜索服务选择、配置、测试、保存和来源链接查看。
