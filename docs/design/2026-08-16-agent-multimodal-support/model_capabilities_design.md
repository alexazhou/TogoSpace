# V25 模型能力列表（input）与发送门控设计

## 背景

V25 已实现工具结果图片随 `user` 消息发送给模型（`read_image` → `AgentMessage` 附件 →
发送时拆成「TOOL 文本 + USER 图片」）。但**不是所有模型都支持读图（vision）**：

- 当前 `LlmModelConfig` 只有布尔 `support_vision`，无法表达"支持文本/图片/音频…"等
  多种能力，也无法为后续音频、视频等模态扩展。
- 若把图片消息发给不支持图片的模型，OpenAI 协议会直接报错（或模型收到无法理解的
  `image_url` 块），导致推理失败。

因此需要：模型配置声明**能力列表**，发送时据此门控——非视觉模型不发送图片消息。

## 目标（本次范围）

1. 模型配置支持**能力列表**（text / image / audio…）。
2. 旧配置自动迁移（**配置版本 v2 → v3**），`support_vision` 无缝迁移到 `input`。
3. **发送门控**：模型不具备 `image` 能力时，`read_image` 的图片附件不进入发送消息
   （只保留 TOOL 文本），避免非视觉模型收到 `image_url` 报错。

工具暴露门控（无 image 能力不提供 `read_image`）与 vision 槽位校验暂不在本次范围，
留作后续。

## 1. 能力枚举

在 `src/constants.py` 新增 `ModelInput`，继承 `EnhanceEnum`（配置字段 → 显式字符串
value，符合枚举规范）：

```python
class ModelInput(EnhanceEnum):
    TEXT  = "text"     # 文本（基线能力，所有模型默认支持）
    IMAGE = "image"    # 图片/视觉（可读图）
    AUDIO = "audio"    # 音频
    VIDEO = "video"    # 视频（预留，便于后续扩展）
```

- `text` 作为基线能力显式包含，列表语义完整、扩展成本低。
- 后续新增能力（如 `document`）只需在枚举加一项。

## 2. 配置字段变更

`src/util/configUtil/configTypes.py` 的 `LlmModelConfig`：

```python
class LlmModelConfig(BaseModel):
    name: str
    protocol: LlmProtocol
    enabled: bool = True
    input: list[ModelInput] = [ModelInput.TEXT]   # 取代 support_vision
    temperature: Optional[float] = None
    extra_params: dict[str, Any] = Field(default_factory=dict)
    extra_headers: dict[str, str] = Field(default_factory=dict)
    context_config: Optional[LlmContextConfig] = None

    @model_serializer(mode="wrap")
    def _serialize(self, handler: Any) -> dict[str, Any]:
        data = handler(self)
        # 纯文本模型（input == 默认 ["text"]）不落盘 input key
        if data.get("input") in (None, [ModelInput.TEXT.value]):
            data.pop("input", None)
        cc = data.get("context_config")
        if cc is None or cc == {} or cc == LlmContextConfig().model_dump(mode="json"):
            data.pop("context_config", None)
        return data
```

- 缺省即纯文本：配置里没有 `input` key 的模型，pydantic 默认 `["text"]`。
- 视觉模型显式配 `input: ["text", "image"]`。
- **序列化时 `input == ["text"]` 不写 key**，只把非纯文本能力落盘（与 `context_config`
  的缺省不落盘一致）。
- `support_vision` 字段废弃，由迁移逻辑处理旧数据。

## 3. 配置迁移 v2 → v3

参考 `src/util/configUtil/migrations/v1_to_v2.py` 的实现模式。

### 3.1 新增 migrate 文件 `migrations/v2_to_v3.py`

```python
# src/util/configUtil/migrations/v2_to_v3.py
def migrate_v2_to_v3(cfg: dict) -> None:
    """向后兼容自动迁移 (V2 -> V3)：support_vision → input"""
    if cfg.get("version", "v1") != "v2":
        return
    for provider in cfg.get("llm_providers", []):
        for model in provider.get("models", []):
            if "input" in model:
                continue              # 已显式配置，跳过
            if model.get("support_vision"):
                model["input"] = ["text", "image"]   # 仅视觉模型写 input
            model.pop("support_vision", None)        # 纯文本模型不写 input，靠默认
    cfg["version"] = "v3"
```

迁移规则：
- 已存在 `input` key 的模型跳过（幂等，尊重显式配置）。
- 旧 `support_vision: true` → 写入 `input=["text","image"]`。
- 纯文本模型（无 `support_vision`）**不写 `input` key**，靠 pydantic 默认 `["text"]`。
- 一律移除 `support_vision`，`version` 升为 `v3`。

### 3.2 接入 `migrations/__init__.py`

```python
def migrate_setting(cfg: dict) -> None:
    migrate_v1_to_v2(cfg)
    migrate_v2_to_v3(cfg)   # 追加
```

### 3.3 默认版本

`configTypes.py` 的 `SettingConfig.version: str = "v2"` 改为 `"v3"`。

## 4. 发送门控（暂缓，后续实现）

> **状态**：本次只做配置迁移（`input` 字段落库 + v2→v3），**发送门控暂不实现**，
> 后续按模型 `input` 能力接入。

### 设计（预留）

`src/service/llmService/core.py` 的 `_split_tool_result_messages` 增加
`image_supported: bool` 参数，仅在支持图片时产出图片 USER 消息：

```python
def _split_tool_result_messages(
    messages: list[AgentMessage],
    *,
    image_supported: bool,
) -> list[llmApiUtil.OpenAIMessage]:
    for msg in messages:
        converted.append(msg.to_openai_message())
        if image_supported:   # 非视觉模型不产出图片消息
            for att in (msg.attachments or []):
                if att.kind == "image":
                    converted.append(build_image_user_message(att))
    ...
```

`_build_request` 传入：

```python
image_supported = ModelInput.IMAGE in (model_config.input or [])
messages = [
    OpenAIMessage.text(SYSTEM, ctx.system_prompt),
    *_split_tool_result_messages(ctx.messages, image_supported=image_supported),
]
```

预期行为：
- **视觉模型**（`input` 含 `image`）：图片拆成 USER 图片消息发送。
- **非视觉模型**（仅 `text`）：图片附件不进入发送消息，TOOL 文本照常发送，不会触发
  OpenAI 协议报错。
- 历史记录不受影响：附件始终完整持久化，门控只在发送边界生效。

## 5. 边界与兼容性

- **配置兼容**：旧 `setting.json`（v2，含 `support_vision`）启动时自动迁移到 v3，
  无感升级，无需手动改配置。
- **DB 兼容**：能力列表只存在于配置，不涉及数据库表结构。
- **发送语义**：门控是"下发时裁剪"，不改变历史存储内容。
- **本次不做**：发送门控（§4，后续实现）、按能力过滤 `read_image` 工具暴露、vision
  槽位校验、音频/视频的发送处理。

## 6. 涉及文件

| 文件 | 改动 |
|------|------|
| `src/constants.py` | 新增 `ModelInput` 枚举 |
| `src/util/configUtil/configTypes.py` | `LlmModelConfig.input` 取代 `support_vision`；`SettingConfig.version` 默认 `v3` |
| `src/util/configUtil/migrations/v2_to_v3.py` | 新增迁移逻辑 |
| `src/util/configUtil/migrations/__init__.py` | `migrate_setting` 追加 `migrate_v2_to_v3` |
| `src/service/llmService/core.py` | （后续）`_split_tool_result_messages` 增加 `image_supported`；`_build_request` 传入 |
| 测试 | 迁移测试、序列化测试 |
