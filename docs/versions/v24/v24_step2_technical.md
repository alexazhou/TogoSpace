# V24: 模型配置体系重构 - 技术文档

## 1. 方案概览

### 1.1 目标

重构底层的大模型配置体系，从“服务直连模型”的扁平结构，升级为“提供商(Provider) -> 模型(Model)”的两级结构；引入全局模型槽位（主模型、轻量模型、读图模型）以取代单一的 `default_llm_server`；并实现多协议 URL、向后兼容平滑迁移等机制，支持模型级别的连通性测试。

### 1.2 核心挑战

| 编号 | 问题 | 难度 |
|------|------|------|
| ① | 数据结构重构：废弃 `LlmServiceConfig`，引入 `LlmProviderConfig` 和 `LlmModelConfig`，同时处理旧配置的无缝向下兼容和自动迁移 | 中等 |
| ② | 预置 URL 机制：不同提供商对应不同的协议（OpenAI、Anthropic 等），需支持配置预置 URL 与用户自定义覆盖 | 中等 |
| ③ | 路由逻辑重构：Agent 运行时解析模型（`resolve_model`），需支持直接指定（`model@provider`）与槽位引用（`primary`等） | 简单 |
| ④ | 连通性测试：需同时支持提供商级别和具体模型级别的 API 连通性测试 | 简单 |
| ⑤ | 前后端交互升级：大量涉及大模型配置和使用的前端页面与后端接口需要重构适配 | 繁琐 |

### 1.3 改动范围概览

| 层 | 改动内容 |
|----|---------|
| **数据模型** | `configTypes.py` 新增 `LlmProviderConfig`、`LlmModelConfig`、`DefaultModelSlots`，废弃 `LlmServiceConfig` |
| **配置文件** | 新增 `assets/preset/providerDefaultUrls.json` 存储预置的各厂商 URL |
| **配置加载与迁移** | `configUtil.py` 实现旧版 `llm_services` 格式向新版 `llm_providers` 的自动迁移映射 |
| **运行时路由** | `llmService/core.py` 中重构 `resolve_model` 逻辑，支持槽位和特定模型解析 |
| **Token 参数读取** | `agentService/compact.py` 调整，使其从新的 `LlmModelConfig` 中读取 token 参数 |
| **API 接口** | `settingController.py` 新增提供商管理、模型管理、模型槽位配置及测试相关的多组 API |
| **路由** | `route.py` 注册新增配置及测试路由，废除旧有配置路由 |
| **前端组件** | 改造 `ModelsSettingsSection.vue` 和 `ModelServiceEditorDialog.vue` 等前端组件；新增预置逻辑 `useProviderPresets.ts` |

---

## 2. 数据模型与核心配置

### 2.1 LlmProviderConfig (提供商配置)

```python
class LlmProviderConfig(BaseModel):
    """LLM 提供商配置 — 对应一个 API 服务提供商。"""
    name: str                          # 提供商唯一标识，如 "openai-main"、"my-aliyun"（不可包含 "@" 字符）
    type: str                          # 提供商类型，如 "openai"、"aliyun"、"deepseek"（决定预置 URL）
    api_key: str                       # API 密钥
    enable: bool = True                # 是否启用
    urls: dict[str, str] = {}          # 自定义 URL 映射，如 {"openai": "https://...", "anthropic": "https://..."}
                                       # 为空时使用预置 URL
    extra_headers: dict[str, str] = {} # 附加请求头
    provider_params: dict[str, Any] = {} # Provider 特定参数
    models: list[LlmModelConfig] = []  # 该提供商下的模型列表
```

### 2.2 LlmModelConfig (模型配置)

```python
class LlmContextConfig(BaseModel):
    """上下文与压缩策略配置"""
    context_window_tokens: int = 128000
    reserve_output_tokens: int = 4096
    compact_trigger_ratio: float = 0.85
    compact_summary_max_tokens: int = 6144

class LlmModelConfig(BaseModel):
    """单个模型的配置 — 归属于某个提供商。"""
    name: str                          # 模型标识，如 "gpt-4o"、"qwen-plus"（不可包含 "@" 字符）
    enabled: bool = True               # 是否启用
    support_vision: bool = False       # 是否支持多模态（读图）
    temperature: float | None = None   # 默认温度
    provider_params: dict[str, Any] = {}  # 模型级参数，覆盖 provider 的 provider_params

    # 独立的上下文与压缩策略（若配置，将覆盖全局的 context_config）
    context_config: LlmContextConfig | None = None

    # 协议配置（可选）
    protocol: str | None = None          # 指定该模型使用的协议，如 "openai"；None 表示系统自动选择
```

### 2.3 DefaultModelSlots (全局默认模型槽位)

```python
class DefaultModelSlots(BaseModel):
    """全局默认模型槽位。"""
    primary: str = ""        # 主模型，如 "gpt-4o@openai-main"
    lightweight: str = ""    # 轻量级模型，如 "gpt-4o-mini@openai-main"
    vision: str = ""         # 读图模型，如 "qwen-vl-max@my-aliyun"

class AppSettings(BaseModel):
    """系统全局配置"""
    version: str = "v2"                                    # 配置文件版本号
    llm_providers: list[LlmProviderConfig] = []
    default_models: DefaultModelSlots = DefaultModelSlots()
    context_config: LlmContextConfig = LlmContextConfig()  # 全局上下文配置
```

---

## 3. 核心机制

### 3.1 预置 URL 解析规则
系统将通过 `assets/preset/providerDefaultUrls.json` 文件存储预置映射。
合并规则：
`最终 URL = 预置 URL + 用户自定义 URL (urls 字段)`。当同 key（协议名）冲突时，用户自定义配置优先级更高。

预置字典样例：
```json
{
  "openai": { "label": "OpenAI", "openai": "https://api.openai.com/v1" },
  "aliyun": { 
      "label": "阿里云（通义千问）", 
      "openai": "https://dashscope.aliyuncs.com/compatible-mode/v1", 
      "anthropic": "https://dashscope.aliyuncs.com/apps/anthropic" 
  }
}
```

### 3.2 Agent 模型解析逻辑 (resolve_model)

在推理入口（`llmService/core.py` 附近）实现 `resolve_model` 函数，负责将 Agent 的 `model` 字段解析为具体的 Provider 和 Model 配置：
```python
def resolve_model(agent_model: str | None) -> tuple[LlmProviderConfig, LlmModelConfig, str]:
    # 1. 降级：如果 agent_model 为 None 或空，默认使用 "primary" 槽位的值
    # 2. 槽位解析：如果是 "primary", "lightweight", "vision"，查找 DefaultModelSlots 配置对应的具体模型地址（如 "gpt-4o@openai-main"）
    # 3. 具体指定解析：解析 "model@provider" 格式（由 '@' 分割），在系统中查找匹配的 LlmProviderConfig 和 LlmModelConfig
    # 4. 协议选择：根据 LlmModelConfig.protocol 和 provider 实际可用的 URL key，选择合适的调用协议 (protocol)
    # 5. 返回 (provider_config, model_config, protocol)
    pass
```

### 3.3 向后兼容自动迁移与版本机制

引入配置文件版本号机制，新版配置设定为 `version = "v2"`。

在系统启动加载配置（`configUtil.py`）时注入拦截：
1. **版本检测**：检查 `setting.json` 中是否存在 `version` 字段。若无该字段或为 `"v1"`，则认定为 v1 版旧配置，触发向 v2 的自动迁移。
2. **数据结构转换**：遍历旧配置的 `llm_services` 结构，将其转换为 `llm_providers`：
   - 映射 `type`: `openai-compatible` -> `openai`，并将其 `base_url` 写入 `urls["openai"]` 中。
   - 将原有的 `model` 包装进 `models` 列表中。
   - `temperature` 等通用参数平移至 `LlmModelConfig`。
3. **槽位映射**：将旧版的 `default_llm_server`（如 "default"），映射转换并写入到 `default_models.primary`，指代到 `model@default` 上。
4. **保存升级**：移除 `llm_services` 等旧字段，将 `version` 置为 `"v2"` 并覆盖保存。后续若有更多迭代，将严格基于版本号进行迁移调度。

---

## 4. 后端 API 设计

### 4.1 提供商 (Provider) 管理

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/config/llm_providers/preset_urls.json` | 获取所有预置提供商的默认 URL |
| `GET` | `/config/llm_providers/list.json` | 获取所有提供商及模型列表 |
| `POST` | `/config/llm_providers/create.json` | 新增提供商（可同时添加初始模型） |
| `POST` | `/config/llm_providers/{name}/modify.json` | 修改提供商配置 |
| `POST` | `/config/llm_providers/{name}/delete.json` | 删除提供商 |
| `POST` | `/config/llm_providers/{name}/test.json` | 测试提供商基本连通性 |

### 4.2 模型 (Model) 管理

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/config/llm_providers/{name}/models/add.json` | 向特定提供商添加新模型 |
| `POST` | `/config/llm_providers/{name}/models/{model_name}/modify.json` | 修改模型属性 |
| `POST` | `/config/llm_providers/{name}/models/{model_name}/delete.json` | 删除模型 |
| `POST` | `/config/llm_providers/{name}/models/{model_name}/test.json` | 测试单一模型的连通性（本期新增特性） |

### 4.3 全局模型槽位管理

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/config/default_models.json` | 获取当前的全局默认模型槽位配置 |
| `POST` | `/config/default_models.json` | 更新全局默认模型槽位配置 |

---

## 5. 测试要点

1. **兼容性启动**：放置旧版 `setting.json` (包含 `llm_services`)，启动服务后，配置文件应成功自动改写为 `llm_providers` 的新格式，且 `primary` 槽位被正确赋值。
2. **多模型解析**：配置不同类型的槽位，验证 Agent 在不指定模型、指定槽位、直接指定 (`model@provider`) 时的正确路由。
3. **预置覆盖**：验证新建某预置类型提供商时能否获取预置 URL，验证用户通过 `urls` 配置修改后，生效的是自定义配置而非预置。
4. **模型连通性测试**：调用独立的模型连通性测试接口（附带对应的 provider 鉴权信息），能够正确返回成功或识别鉴权/网络失败，并在前端反馈。
5. **协议自适应**：验证针对 `aliyun` 等多协议 provider，能否根据模型定义的 `protocol` 成功采用对应协议和对应的自定义/预置 URL 路径。
