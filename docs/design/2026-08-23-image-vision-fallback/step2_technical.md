# Agent 图片视觉 Fallback（视觉模型兜底）- 技术文档

> 状态：规划中（尚未实施）

## 架构概览

**分工明确**：
- **agentService 负责「识别」**：`agentTurnRunner._infer_to_item`（L427，推理执行方法）在调用 llmService 之前，调用 `visionFallback.recognize`，对无 caption 的图片附件调用视觉模型识别，结果写回 `MessageAttachment.caption` 并持久化。
- **llmService 负责「插入」**：`_split_tool_result_messages` 在 AgentMessage → OpenAIMessage 转换时，识别到模型不支持视觉，则不生成图片 block，改为**插入对应文本消息**（有 caption 插入 caption 文本，无 caption 插入"无法读取"说明）。

```
agentTurnRunner._infer_to_item（agentService，L427）
  ├─ messages = history.build_infer_messages()          # AgentMessage 列表（含图片附件）
  ├─ [场景 B] await visionFallback.recognize(messages)  # ① 识别写 caption + 显式持久化（仅无 caption 的图）
  ├─ ctx = GtCoreAgentDialogContext(messages=messages)
  └─ await llmService.infer_stream(agent.model, ctx)

llmService（发送层）
  └─ resolve_model(model) → model_config                # 拿到 input 能力
       └─ _build_request(ctx, model_config)
            └─ _split_tool_result_messages(messages, supports_vision)   # ★ 插入消息唯一落点
                 └─ 对图片附件按能力分支：
                     ├─ 支持视觉       → 原样生成 image_url block（V25 流程）
                     ├─ 不支持 + 有 caption → 插入 caption 文本消息（不生成 image_url）
                     └─ 不支持 + 无 caption → 插入"无法读取图片"说明文本
```

核心思路：**识别是业务动作，插入是发送动作**。agentService 只负责"把图片变成有文本描述"（写 caption）；llmService 在转换层按「模型能力 + caption 有无」决定最终消息形态，从源头保证图片不会传到不支持视觉的 LLM 服务端。

## 命名落地

| 概念 | 命名 |
|------|------|
| 新增模块 | `src/service/agentService/visionFallback.py` |
| 视觉模型识别 | `visionFallback.recognize(messages) -> None`（识别 + 写 caption + 持久化） |
| 识别提示词 | `agentService/prompts.py::VISION_RECOGNIZE_PROMPT`（默认：请描述这张图片的内容，包括文字、图表、界面元素等关键信息，供 agent 后续决策使用） |
| 无视觉说明文案 | `llmService/core.py::VISION_UNAVAILABLE_PROMPT` |
| fallback 开关 | `auto_vision_fallback`（默认 `true`，已确认） |
| 视觉能力判断 | `ModelInput.IMAGE in model_config.input`（`LlmModelConfig.input`） |
| 视觉模型槽位 | `DefaultModelSlots.vision`（引用 `vision@system`） |
| 图片文本描述 | `MessageAttachment.caption`（复用现有字段，识别结果写入） |
| 转换层插入函数 | `_split_tool_result_messages(messages, supports_vision)`（加参数） |

## 决策记录

| 决策点 | 结论 | 说明 |
|--------|------|------|
| 配置字段 | **不新增**，复用 `input` 能力字段 + `vision` 槽位 | `LlmModelConfig.input` 已支持 `IMAGE`，`default_models.vision` 已存在 |
| fallback 开关 | **`auto_vision_fallback` 默认开启** | 用户可按需关闭；关闭后场景 B 按场景 C 处理 |
| 识别结果形态 | **纯文本描述**，v1 不做 JSON 结构化 | 简单可靠，后续需要再增强 |
| 无法读取提示 | **每张图触发一次** | 逐图注入，避免多条图片合并成一条导致表述不精确 |
| 前端 UI | **不突出视觉模型槽位**，保持现状 | 已有"视觉模型"标签，不做额外引导 |
| 分工 | **agentService 识别写 caption；llmService 转换层插入消息** | 识别是业务动作（含持久化），插入是发送动作（转换时决策） |
| 附件处理 | **历史保留原图，识别结果写 `caption` 持久化** | 插入只影响发送时的 OpenAIMessage，不修改 AgentMessage、不污染历史 |

## 核心改动点

### ① 视觉模型识别（agentService：visionFallback.recognize，可选前置步骤）

**机制**：仅当「主模型不支持视觉 + `default_models.vision` 已配 + `auto_vision_fallback` 开启」时，在 `agentTurnRunner._infer_to_item`（L427）中、调用 llmService 之前，对本轮上下文中**无 caption 的图片附件**（`MessageAttachment(kind="image")`）逐张调用视觉模型识别：

- `resolve_model("vision@system")` 取视觉模型配置 → 复用 `llmService` 的 provider/客户端体系发起非流式调用 → 输入仅含 `VISION_RECOGNIZE_PROMPT` + 图片（`image_url` data URL），不携带完整对话历史（省 token、聚焦识别）。
- **识别结果（纯文本描述）写回该图片附件的 `caption` 字段**，作为该图片的"文本描述"长期复用（下次 turn 无需重新识别）。

**持久化（关键，避免重复识别）**：`build_infer_messages()` 返回的是 `AgentHistoryStore._items` 中 `GtAgentHistory.message` 的**内存引用**（`agentHistoryStore.py` L377），直接修改 `caption` 只影响内存、**不会自动写回数据库**。因此 `recognize` 写 caption 后必须**显式持久化**：

```
for msg in messages:
    if 该 msg 的图片附件 caption 被更新:
        history_id = 找到 msg 对应的 GtAgentHistory.id   # 在 AgentHistoryStore 中按 message 引用反查
        await gtAgentHistoryManager.update_agent_history_by_id(
            history_id,
            message=msg,          # message 为 PydanticJsonField，整条序列化落库
        )
```

持久化后：内存与 DB 都带 caption，**进程重启 / `reload_from_db()` 后不丢失**，同一图片不会重复识别。识别去重依据：**`recognize` 只对「无 caption 的图片附件」发起调用**，已有 caption（之前识别过 / 工具结果自带说明）直接跳过。

**识别口径**：
- 多张图片逐张识别，每张返回一条文本（v1 简单可靠）。
- 识别结果长度限制：截断为约 500 字摘要，避免污染主模型上下文。
- 写 caption 时保留必要的引导语（如工具结果图片的 `"以下是工具执行结果的图片（xxx）："`）或直接以识别文本替换，保证"有 caption = 有可读内容"。

**失败处理**：识别失败（超时 / 报错 / 视觉模型不可用）→ caption 保持为空，由转换层按"无 caption"插入说明，不阻塞主流程；异常记日志。

**兜底场景**：主模型读不了图、但系统配了视觉模型时，让 agent 仍能"看图"。

### ② 转换层插入消息（llmService：_split_tool_result_messages，核心决策点）

**机制**：`_split_tool_result_messages`（`llmService/core.py` L165，AgentMessage → OpenAIMessage 转换处）增加 `supports_vision: bool` 参数，由 `_build_request`（L211）在 `resolve_model` 后按 `ModelInput.IMAGE in model_config.input` 传入。转换时对每个图片附件按能力分支**插入对应消息**：

```
supports_vision = ModelInput.IMAGE in model_config.input

对每个图片附件（USER 图片消息 / TOOL 结果图拆分）：
├─ supports_vision == True
│     → 原样生成 OpenAIImageUrlContentBlock（V25 现有流程不变）
├─ supports_vision == False 且 attachment.caption 非空
│     → 插入 OpenAITextContentBlock(text=caption)，不生成 image_url
│       （TOOL 图场景：TOOL 文本消息照旧，图片 USER 消息替换为 caption 文本消息）
└─ supports_vision == False 且 attachment.caption 为空
      → 插入 OpenAITextContentBlock(text=VISION_UNAVAILABLE_PROMPT)，不生成 image_url
```

**判定口径**：
- `supports_vision` 来自 `resolve_model(agent.model)` 返回的合并配置 `input`（已处理 `model@provider` / `slot@system` / 空值默认 `primary@system`）。
- `caption` 是否"非空"：`attachment.caption` 为 `None` 或空白字符串视为无说明。

**效果保证**：不支持视觉时，转换层**根本不生成 `image_url` content block**——图片 base64 不会进入请求体，**不会传到主模型 LLM 服务端**；插入的是纯文本消息（caption / 说明），主模型可正常推理。

**多张图片**：逐张处理；有 caption 的插入 caption 文本，无 caption 的逐张插入说明（每张图一条），不丢失"图存在"这一事实。

**兜底场景**：主模型不支持视觉时，避免模型 API 报错或静默忽略图片，同时保证请求体不带图片。

### ③ 无视觉说明插入（转换层内）

**机制**：不支持视觉且附件无 caption 时，由转换层直接生成说明文本（`VISION_UNAVAILABLE_PROMPT`，每张图一条，可带图序）：

> 你收到了一条图片消息，但当前配置的模型不支持视觉（图片识别），因此无法读取图片内容。请忽略该图片，并告知操作者当前模型无法处理图片，或建议配置视觉模型后再重试。

**目的**：让 agent 明确知道"图片存在但读不了"，从而不臆造图片内容、主动向用户说明限制、引导配置视觉模型。

**触发位置**：转换层对每个「不支持视觉且无 caption」的图片附件插入一次（v1 按图逐张，已确认）。

## 执行位置与调用链（图片转文本落点）

「图片转文本」由两部分组成：**agentService 识别写 caption**（前置） + **llmService 转换层插入消息**（最终决策）。

> `_infer_to_item`（L427）是实际执行推理的方法，调用链：`_advance_step`（L337）→ `_infer_and_classify`（L406）→ `_infer_to_item`（L427，内部 L440 调 `resolve_model`、L459 调 `build_infer_messages`、L519 调 `llmService.infer_stream`）。

```
agentTurnRunner._infer_to_item（agentService，L427）
  ├─ messages = history.build_infer_messages()          # AgentMessage 列表（含图片附件，内存引用）
  ├─ provider_config, model_config = resolve_model(agent.model)   # 已有（L440）
  ├─ supports_vision = ModelInput.IMAGE in model_config.input
  ├─ [场景 B] await visionFallback.recognize(messages)  # ① 识别写 caption + 显式持久化（仅无 caption 的图）
  ├─ ctx = GtCoreAgentDialogContext(messages=messages)
  └─ await llmService.infer_stream(agent.model, ctx)    # ③ 走 llmService

llmService（发送层）
  └─ _build_request(ctx, model_config)                  # ② 计算 supports_vision 传入
       └─ _split_tool_result_messages(messages, supports_vision)   # ★ 插入消息唯一落点
```

| 步骤 | 位置 | 职责 |
|------|------|------|
| ① 识别写 caption | `agentTurnRunner._infer_to_item`（L427，`build_infer_messages()` 之后、构造 ctx 之前） | 仅场景 B 调用 `visionFallback.recognize`：对无 caption 的图片识别，结果写回 `MessageAttachment.caption`，并**显式持久化**（`update_agent_history_by_id`） |
| ② 能力判断 | `llmService/core.py::_build_request`（L211） | 用已解析的 `model_config` 计算 `supports_vision = ModelInput.IMAGE in model_config.input` |
| ★ 插入消息 | `llmService/core.py::_split_tool_result_messages`（L165） | 签名加 `supports_vision: bool`，转换时对图片附件三分支：发图 / 插入 caption 文本 / 插入说明文本 |

关键点：

1. **`_split_tool_result_messages` 是插入消息的唯一落点**：它把 `ctx.messages`（AgentMessage）转成 `OpenAIMessage`（`_build_request` L219 调用），图片在此时此地变成 `image_url` content block——因此也在这里拦截，才能保证图片不进请求体。
2. **`supports_vision` 由 `_build_request` 传入**：`_build_request` 已持有 `model_config`（`resolve_model` 返回），直接算 `ModelInput.IMAGE in model_config.input`，不需要额外解析。
3. **识别是 agentService 的前置动作**：`visionFallback.recognize` 只对「无 caption 的图片附件」调用；已有 caption（之前识别过/工具结果自带说明）直接复用，转换层读 caption 即可。
4. **插入只影响 OpenAIMessage，不改 AgentMessage**：转换层生成的是发送用消息，`AgentMessage` 与历史原样保留（含图片附件），不污染历史。
5. **`infer` / `infer_stream` 都受益**：两者都走 `_build_request` → `_split_tool_result_messages`，转换层改动同时覆盖流式与非流式主模型调用。
6. **`to_openai_message` 保持原样**：`AgentMessage.to_openai_message()`（agentMessage.py L101）与 `compact.py`（L83 token 计数）也在用，不改其签名；能力分支统一在 `_split_tool_result_messages` 内处理，避免侵入存储层与 token 计数逻辑。

## 兼容性

- **不改变主模型链路**：场景 A（支持视觉）完全走 V25 现有流程；`infer / infer_stream` 签名不变，仅 `_split_tool_result_messages` 增加一个能力参数。
- **不新增配置字段**：视觉能力判断复用 `LlmModelConfig.input`；视觉模型配置复用 `default_models.vision` 槽位；新增仅 `auto_vision_fallback` 开关（沿用 `CONFIG_DEFAULTS` / 设置页机制）。
- **附件结构不变**：`MessageAttachment` 结构不变，仅复用其 `caption` 字段存识别结果；历史消息中的图片附件始终保留（识别结果也随附件持久化，天然缓存）。
- **各前端形态一致**：判断与插入逻辑在 host 侧 `llmService` 转换层内，Web Console / TUI / 桌面 App 等任何入口触发 agent 推理时行为一致，与前端形态无关。

## 与既有逻辑的关系

- **V25 多模态（`_split_tool_result_messages` / `to_openai_message`）**：图片拆 USER 消息、`image_url` 仅允许在 user 角色的既有约束不变；场景 A 原样复用，场景 B/C 在转换时按 `supports_vision` 分支决定生成 image_url 还是插入纯文本。
- **`MessageAttachment.caption`**：现有字段（说明文字，发送时可转成文本块），本次赋予"图片文本描述"语义：识别结果写入后，转换层可将其作为不支持视觉时的文本替代；现有 `from_tool_result` 生成的引导语 caption 保持兼容（有引导语即视为有说明）。
- **`resolve_model` 能力判断**：`resolve_model(agent.model)` 返回合并配置可直接读 `input`；`resolve_model("vision@system")` 用于取视觉模型，未配置时抛 `ValueError`（在 `visionFallback.recognize` 内捕获并跳过识别，走无 caption 分支）。
- **`llmService.infer / infer_stream`**：视觉识别使用 `infer`（非流式）独立调用，不侵入主模型推理；主模型调用仍走原入口。
- **模型能力门控现状**：`_split_tool_result_messages` 注释中"模型能力门控暂未启用"即本方案要补齐的点——在转换层用 `input` 能力字段做门控，把"能力判断"从模型 API 报错/静默忽略前移为显式分支。
