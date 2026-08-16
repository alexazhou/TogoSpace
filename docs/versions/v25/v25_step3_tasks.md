# V25: Agent 历史消息类型与多模态扩展 - 开发任务表

## 任务概览

V25 的目标是把 `GtAgentHistory.message` 从 `OpenAIMessage` 改为独立的自定义类型 `AgentMessage`，
并让 `OpenAIMessage` 支持多模态 content，使工具结果图片可以随 `user` 消息发送给模型识别。

本版本的核心范围包括：

- `OpenAIMessage.content` 多模态支持（text / image_url content block）
- 新增 `AgentMessage` / `MessageAttachment` 自定义存储类型（不继承 `OpenAIMessage`）
- `GtAgentHistory` 及 Store 层接入新类型，统一包装 / 转换边界
- 工具结果图片挂 TOOL 消息附件，发送时由 llmService 拆成「TOOL 文本 + 独立 USER 图片消息」
- 发送边界角色约束（仅 user 角色携带图片），符合 OpenAI 规范
- 旧历史数据无缝兼容，无迁移

共拆分为 7 个任务，按依赖关系排序。

---

## 任务列表

### 任务 1: `OpenAIMessage` 多模态支持

**描述**: 在 `OpenAiModels.py` 中新增多模态 content block 类型，扩展 `OpenAIMessage.content`，并提供 `text_content()` 统一文本提取。

**依赖**: 无

**文件**:
- `src/util/llmApiUtil/OpenAiModels.py`（修改）
- `src/util/llmApiUtil/__init__.py`（修改，导出新类型）

**子任务**:
- [x] 新增 `OpenAITextContentBlock`（`type: Literal["text"]`, `text: str`）
- [x] 新增 `OpenAIImageUrlContentBlock`（`type: Literal["image_url"]`, `image_url: dict`）
- [x] 定义 `OpenAIContentBlock = Annotated[Text | ImageUrl, Field(discriminator="type")]`
- [x] `OpenAIMessage.content` 类型扩展为 `str | list[OpenAIContentBlock]`
- [x] 新增 `OpenAIMessage.text_content()`：str 原样返回，blocks 数组拼接 text 块
- [x] 在 `util/llmApiUtil/__init__.py` 导出新类型
- [x] 验证 `to_dict()` 对多模态 content 的序列化（`model_dump` 天然输出块数组）
- [x] 验证 `litellm.token_counter` 能处理多模态 content

**验收标准**:
- `OpenAIMessage` 既能存字符串 content，也能存 blocks 数组 content。
- `model_dump_json` / `model_validate_json` 往返不丢数据。
- `text_content()` 对 str / blocks 两种形态均正确提取文本。
- 旧字符串 content 的既有行为完全不变。

---

### 任务 2: 新增 `AgentMessage` / `MessageAttachment`

**描述**: 新建独立于 `OpenAIMessage` 的自定义存储类型，承载 openai 语义字段 + 附件。

**依赖**: 任务 1

**文件**:
- `src/model/dbModel/agentMessage.py`（新建）

**子任务**:
- [x] 新增 `MessageAttachment`（`kind` / `mime_type` / `data` / `url` / `caption`）
- [x] 新增 `AgentMessage(BaseModel)`，字段：`role / content(str) / reasoning_content / tool_calls / tool_call_id / attachments`
- [x] 实现 `AgentMessage.from_openai()`：`content` 用 `msg.text_content()`，多模态 content 只保留文本
- [x] 实现 `AgentMessage.to_openai_message()`：仅在 `role == USER` 且存在附件时拼 content blocks，其余角色保持纯文本
- [x] 附件转 image_url 时，`url` 优先；有 `data` 时拼 `data:{mime_type};base64,{data}`

**验收标准**:
- 旧 `OpenAIMessage` JSON 可直接 `AgentMessage.model_validate_json()` 读回（兼容）。
- 带附件消息经 `model_dump_json` / `model_validate_json` 往返不丢附件。
- `to_openai_message()` 对 `tool` / `system` / `assistant` 角色不产出 `image_url`（角色约束生效）。
- 无附件消息 `to_openai_message()` 输出与改造前一致。

---

### 任务 3: 改造 `GtAgentHistory`

**描述**: `message` 字段类型改为 `AgentMessage`，`build()` 只接受 `AgentMessage`，`openai_message*` 属性统一转换出口。

**依赖**: 任务 2

**文件**:
- `src/model/dbModel/gtAgentHistory.py`（修改）

**子任务**:
- [x] `message` 字段类型改为 `AgentMessage | None = PydanticJsonField(AgentMessage, null=True)`
- [x] `build()` 只接受 `AgentMessage`，不再兼容 `OpenAIMessage`（转换由调用方经 `AgentMessage.from_openai()` 完成）
- [x] `openai_message_or_none` 返回 `message.to_openai_message()`（有消息时），否则 `None`
- [x] `openai_message` 保持"无消息抛错"语义，返回转换后的 `OpenAIMessage`
- [x] `content` 属性返回 `message.content`（`AgentMessage.content` 恒为 str，语义不变）
- [x] 确认 `has_message` / `tool_calls` / `is_tool_call_succeeded` 等保持不变

**验收标准**:
- `GtAgentHistory.build(AgentMessage)` 直接存储且不丢字段（`OpenAIMessage` 由调用方先转换）。
- 读取侧 `openai_message` 返回 `OpenAIMessage`，与现有调用方契约一致。
- 既有 `content` / `tool_calls` 访问语义不变。

---

### 任务 4: 改造 `AgentHistoryStore`

**描述**: Store 层的写入入口统一接受并包装两种消息类型。

**依赖**: 任务 3

**文件**:
- `src/service/agentService/agentHistoryStore.py`（修改）

**子任务**:
- [x] `finalize_history_item()` 只接受 `AgentMessage`，不兼容 `OpenAIMessage`（转换由调用方经 `AgentMessage.from_openai()` 完成）
- [x] `insert_compact_summary()` 只接受 `AgentMessage`
- [x] `finalize_cancel_turn()` 内构造点直接构造 `AgentMessage`
- [x] 确认 `build_infer_messages()` / `get_last_assistant_message()` 走 `openai_message`，无需改动（验证即可）

**验收标准**:
- 历史可存储带附件的 `AgentMessage`，内存与 DB 一致。
- `build_infer_messages()` 输出 `OpenAIMessage` 列表，类型契约不变。
- 既有 compact / cancel-turn 流程行为不变。

---

### 任务 5: 改造 `AgentTurnRunner`（守卫 + 工具图片流）

**描述**: 适配 `content` union 类型的守卫点，工具结果图片挂 TOOL 消息附件（不拆分），拆分/排序由 llmService 发送时完成。

**依赖**: 任务 1、任务 3、任务 4

**文件**:
- `src/service/agentService/agentTurnRunner.py`（修改）
- `src/service/llmService/core.py`（修改）

**子任务**:
- [x] `_detect_json_tool_call_in_content()` 调用处传 `text_content()`，函数内对非 str 输入防御
- [x] `assistant_message.content.strip()` 等字符串用法改用 `text_content()`
- [x] `_check_compact` 日志中 `len(m.content)` 改用 `text_content()`
- [x] `_run_tool_to_item()`：`AgentMessage.from_tool_result()` 写单条 TOOL 消息，图片挂 `attachments`（content 剥 base64），不拆分、不追加 USER 消息
- [x] `llmService._split_tool_result_messages()`：带图片附件的 TOOL 消息拆成「TOOL 文本 + USER 图片」，图片排在所有 TOOL 结果之后（多 tool_call 不插队）
- [x] 确认 `_infer_to_item()` / `_infer_and_classify()` 对 `text_content()` 返回值正常

**验收标准**:
- 历史每工具结果只存一条 TOOL 消息（图片挂附件），tool_call 配对合法。
- 发送时拆成「TOOL 文本 + USER 图片」，图片排在工具结果块之后，多 tool_call 不乱序。
- 无图片时行为与改造前完全一致。
- 多模态 content 下推理流程不因 `content` 为 list 而崩溃。

---

### 任务 6: 其余守卫与类型注解

**描述**: 同步 `content` union 影响到的其余读取点，并更新 DAL 层类型注解。

**依赖**: 任务 1

**文件**:
- `src/service/agentService/compact.py`（修改）
- `src/controller/settingController.py`（修改）
- `src/dal/db/gtAgentHistoryManager.py`（修改）

**子任务**:
- [x] `compact.py`：`estimate_token_by_char()` 用 `text_content()` 估算文本长度
- [x] `compact.py`：`compact_messages()` 的摘要用 `response_message.text_content()`
- [x] `settingController.py`：`response_text` 改用 `message.text_content()`
- [x] `gtAgentHistoryManager.py`：`update_agent_history_by_id` 的 `message` 参数类型注解更新为只接受 `AgentMessage`

**验收标准**:
- 多模态 content 下 token 估算与摘要生成不崩溃。
- DAL 层类型注解与实际存储类型一致，逻辑不变。

---

### 任务 7: 测试补齐

**描述**: 为多模态消息类型、转换边界与工具图片流补齐单元与集成测试。

**依赖**: 任务 1、2、3、4、5、6

**文件**:
- `tests/unit/util/test_token_infrastructure.py`（修改或新增）
- `tests/unit/service/agentService/test_agent_history.py`（修改）
- `tests/unit/service/agentService/test_build_compact_plan.py`（修改）
- `tests/integration/test_agent_history_store/test.py`（修改）
- `tests/integration/test_compact_flow/test.py`（修改）
- 工具图片流相关集成测试（新增）

**子任务**:
- [x] `OpenAIMessage` 多模态 content 序列化 / 反序列化往返测试
- [x] `text_content()` 对 str / blocks 两种形态的提取测试
- [x] `AgentMessage` 旧 `OpenAIMessage` JSON 兼容解析测试
- [x] `AgentMessage` 带附件消息 DB 往返测试
- [x] `AgentMessage.to_openai_message()` 角色约束测试（tool 角色不产出 image_url）
- [x] `GtAgentHistory.build(AgentMessage)` 直接存储测试（`OpenAIMessage` 先经 `from_openai()` 转换）
- [x] `build_infer_messages()` 对带附件消息输出 `OpenAIMessage` 测试
- [x] 工具返回图片 → 单条 TOOL 消息（图片挂附件）+ 发送时拆分「TOOL 文本 + USER 图片」测试
- [x] 无附件消息发送格式与改造前一致的回归测试

**验收标准**:
- 多模态消息类型、转换边界、角色约束有自动化测试覆盖。
- 工具图片流集成测试通过。
- 既有测试全部通过（无回归）。

---

## 任务依赖关系图

```text
任务 1 (OpenAIMessage 多模态)
    └─ 任务 2 (AgentMessage 新类型)
            └─ 任务 3 (GtAgentHistory)
                    └─ 任务 4 (AgentHistoryStore)

任务 1 ──┐
任务 3 ──┼─ 任务 5 (AgentTurnRunner 守卫 + 工具图片流)
任务 4 ──┘

任务 1 ── 任务 6 (其余守卫与类型注解)

任务 1 ─┐
任务 2 ─┤
任务 3 ─┼─ 任务 7 (测试)
任务 4 ─┤
任务 5 ─┤
任务 6 ─┘
```

---

## 开发顺序建议

**推荐顺序**: 任务 1 → 任务 2 → 任务 3 → 任务 4 → 任务 5 → 任务 6 → 任务 7

**并行开发机会**:
- 任务 5 与任务 6 可并行（分别聚焦 `agentTurnRunner` 与其余守卫点）。
- 任务 7 的单元测试可在任务 2 完成后先行编写，集成测试待任务 5 稳定后补齐。

---

## 文件变更清单

### 新增文件

- `src/model/dbModel/agentMessage.py`
- `docs/versions/v25/v25_step1_product.md`
- `docs/versions/v25/v25_step3_tasks.md`

### 重点修改文件

- `src/util/llmApiUtil/OpenAiModels.py`
- `src/util/llmApiUtil/__init__.py`
- `src/model/dbModel/gtAgentHistory.py`
- `src/service/agentService/agentHistoryStore.py`
- `src/service/agentService/agentTurnRunner.py`
- `src/service/agentService/compact.py`
- `src/controller/settingController.py`
- `src/dal/db/gtAgentHistoryManager.py`

---

## 测试检查清单

- [ ] `OpenAIMessage` 多模态 content 序列化 / 反序列化往返正确
- [ ] `text_content()` 对 str / blocks 两种形态均正确提取
- [ ] `litellm.token_counter` 能估算多模态 content
- [ ] 旧 `OpenAIMessage` JSON 可直接读为 `AgentMessage`
- [ ] 带附件消息 DB 往返不丢数据
- [ ] `to_openai_message()` 对 tool / system / assistant 角色不产出 `image_url`
- [ ] `GtAgentHistory.build(AgentMessage)` 直接存储（`OpenAIMessage` 先经 `from_openai()` 转换）
- [ ] `build_infer_messages()` 输出 `OpenAIMessage` 列表
- [ ] 工具返回图片时历史存单条 TOOL 消息（图片挂附件），发送拆成「TOOL 文本 + USER 图片」且不乱序
- [ ] 无附件消息发送格式与改造前完全一致（回归）
- [ ] compact / cancel-turn 流程在多模态 content 下不崩溃

---

## 验收标准（最终）

- [ ] `GtAgentHistory.message` 可存储携带图片附件的 `AgentMessage`
- [ ] 旧历史数据无需迁移即可正常读取与发送
- [ ] 工具结果图片以 `user` 消息多模态格式发送给模型，符合 OpenAI 规范
- [ ] TOOL / SYSTEM / ASSISTANT 消息不携带 `image_url`
- [ ] 无附件消息的发送行为与改造前完全一致
- [ ] 既有单元 / 集成测试全部通过
