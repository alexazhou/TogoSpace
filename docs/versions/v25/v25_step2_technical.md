# V25: Agent 历史消息类型与多模态扩展 - 技术文档

> 本文档为**设计方案**，尚未落地实现。若代码与本文档冲突，以代码为准。

## 1. 背景与目标

当前 `GtAgentHistory.message` 直接以 `llmApiUtil.OpenAIMessage` 存储。`OpenAIMessage` 只有
`role / content / reasoning_content / tool_calls / tool_call_id` 五个字段，工具结果只能拼成
JSON 字符串塞进 `content`，像「工具调用返回的图片」这类结构化信息没有位置存放。

目标：

1. 将 `GtAgentHistory.message` 改为**自定义类型**（`AgentMessage`），独立于 `OpenAIMessage`，
   持久化 openai 语义字段，额外容纳附件（图片等）。
2. 发送消息时，通过一个方法把 `AgentMessage` 转回 `llmApiUtil.OpenAIMessage`。
3. **本次直接支持多模态发送**：图片附件在发送时转换为 OpenAI 多模态 content block。

## 2. 现状与问题

- `OpenAIMessage.content` 仅支持 `str`，无法表达多模态内容。
- 工具结果以字符串形式存储，图片只能 base64 硬编码进字符串，既膨胀 `content`（发给 LLM 的
  文本也变脏），又无法结构化检索。
- 发送路径 `agentHistoryStore.build_infer_messages()` 直接取 `item.message` 原样给 `llmService`。

## 3. 设计总览

三层结构，职责单一：

| 层 | 类型 | 职责 |
|----|------|------|
| 发送格式 | `OpenAIMessage`（`util`） | 发给 LLM 的标准格式，新增多模态 content block 支持 |
| 存储格式 | `AgentMessage`（`model`） | 独立类型：openai 语义字段 + 附件（图片等） |
| 转换 | `to_openai_message()` / `from_openai()` | 存储 ↔ 发送 的桥 |

写入侧统一只接受 `AgentMessage`（`GtAgentHistory.build()`、Store 层等），
读取侧统一转换出口（`openai_message` 属性），中间业务逻辑几乎零改动。

## 4. `OpenAIMessage` 多模态支持

文件：`src/util/llmApiUtil/OpenAiModels.py`

新增 content block 类型与联合类型（OpenAI 视觉格式）：

```python
class OpenAITextContentBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str

class OpenAIImageUrlContentBlock(BaseModel):
    type: Literal["image_url"] = "image_url"
    image_url: dict[str, str]          # {"url": "data:image/png;base64,..."}

OpenAIContentBlock = Annotated[
    OpenAITextContentBlock | OpenAIImageUrlContentBlock,
    Field(discriminator="type"),
]
```

`OpenAIMessage.content` 扩展为字符串或多模态块数组：

```python
class OpenAIMessage(BaseModel):
    role: OpenaiApiRole
    content: Optional[str | list[OpenAIContentBlock]] = Field(None, description="消息内容")
    reasoning_content: Optional[str] = Field(None, description="推理内容（如 CoT 模型），仅响应侧使用")
    tool_calls: Optional[List[OpenAIToolCall]] = Field(None, description="工具调用列表")
    tool_call_id: Optional[str] = Field(None, description="工具调用 ID（tool 角色专用）")

    def text_content(self) -> str | None:
        """提取纯文本部分：content 为 str 时原样返回；为 content blocks 时拼接 text 块。"""
        if isinstance(self.content, str):
            return self.content
        if isinstance(self.content, list):
            parts = [b.text for b in self.content if isinstance(b, OpenAITextContentBlock)]
            return "".join(parts) or None
        return None
```

`to_dict()` 无需改动——`model_dump(mode="json", exclude_none=True)` 天然输出块数组，
`litellm.token_counter` 亦原生支持多模态 content。

### 4.1 既有调用点守卫

`content` 类型变成 union 后，以下把 `content` 当字符串使用的位置需改用 `text_content()`：

| 文件:行 | 现状 | 改为 |
|---|---|---|
| `agentTurnRunner.py:413` | `_detect_json_tool_call_in_content(assistant_message.content)` | 传 `assistant_message.text_content()` |
| `agentTurnRunner.py:622` | `assistant_message.content.strip()` | `(assistant_message.text_content() or "").strip()` |
| `agentTurnRunner.py:860` | `len(m.content or '')` | `len(m.text_content() or '')` |
| `compact.py:88` | `len(msg.content or "")` | `len(msg.text_content() or "")` |
| `compact.py:143` | `summary = response_message.content or ""` | `summary = response_message.text_content() or ""` |
| `settingController.py:180` | `response_text = message.content or ""` | `message.text_content() or ""` |

## 5. `AgentMessage` 自定义存储类型

文件：`src/model/dbModel/agentMessage.py`（新建）

```python
class MessageAttachment(BaseModel):
    """消息附件（如图片工具结果）。"""
    kind: Literal["image", "file", "text"] = "image"
    mime_type: str | None = None     # image/png
    data: str | None = None          # base64 内联数据
    url: str | None = None           # 或引用路径 / URL（大图推荐，避免撑爆 DB）
    caption: str | None = None       # 说明文字，发送时可转成文本块


class AgentMessage(BaseModel):
    """历史消息自定义类型：独立于 OpenAIMessage，持久化 openai 语义字段 + 附件。"""

    role: OpenaiApiRole
    content: str | None = None                            # 纯文本部分（发给 LLM）
    reasoning_content: str | None = None
    tool_calls: list[OpenAIToolCall] | None = None
    tool_call_id: str | None = None
    attachments: list[MessageAttachment] | None = None     # 图片/文件等放不进 content 的内容

    @classmethod
    def tool_result(cls, tool_call_id: str, result: str) -> "AgentMessage":
        """构造工具调用结果消息（role=TOOL），与 OpenAIMessage.tool_result() 对应。"""

    @classmethod
    def from_openai(cls, msg: OpenAIMessage) -> "AgentMessage":
        """把 OpenAIMessage 转为 AgentMessage（多模态 content 只保留文本部分）。"""
        return cls(
            role=msg.role,
            content=msg.text_content(),
            reasoning_content=msg.reasoning_content,
            tool_calls=msg.tool_calls,
            tool_call_id=msg.tool_call_id,
        )

    def to_openai_message(self) -> OpenAIMessage:
        """发送时转换：user 角色且有附件时拼多模态 content blocks；其余角色保持纯文本。

        OpenAI 规范限制：`image_url` 只允许出现在 `user` 角色的 content 里，
        `tool` / `system` / `assistant` 角色的 content 只支持 text。
        因此附件仅在 `user` 角色生效，其他角色忽略附件。
        """
        content: str | list[OpenAIContentBlock] | None = self.content
        if self.role == OpenaiApiRole.USER and self.attachments:
            blocks: list[OpenAIContentBlock] = []
            if self.content:
                blocks.append(OpenAITextContentBlock(text=self.content))
            for att in self.attachments:
                if att.kind == "image":
                    url = att.url or f"data:{att.mime_type or 'image/png'};base64,{att.data}"
                    blocks.append(OpenAIImageUrlContentBlock(image_url={"url": url}))
                elif att.kind == "text" and att.caption:
                    blocks.append(OpenAITextContentBlock(text=att.caption))
            content = blocks or None
        return OpenAIMessage(
            role=self.role,
            content=content,
            reasoning_content=self.reasoning_content,
            tool_calls=self.tool_calls,
            tool_call_id=self.tool_call_id,
        )
```

**为什么独立于 `OpenAIMessage`（不继承）**：

- `OpenAIMessage` 支持多模态后携带 content block 的复杂结构，继承会把这份复杂度传染给存储
  类型；独立后多模态只存在于 `OpenAIMessage` 与 `to_openai_message()` 中，`AgentMessage`
  保持朴素。
- `AgentMessage.content` 恒为 `str`，`GtAgentHistory.content` 无需 `text_content()` 桥接。
- 无继承则无 forward-ref 的坑（`tool_calls` 直接引用具体类型即可，无需 `model_rebuild()`）。
- 存储与发送两种形态可独立演进。

**多模态的角色约束（OpenAI 规范）**：`image_url` 只允许出现在 `user` 角色消息里，
`tool` / `system` / `assistant` 角色的 content 只支持 text。因此：

- `AgentMessage.attachments` 存储时不限角色（存储宽松），但 `to_openai_message()` 只在
  `role == user` 时把附件转成 image_url（发送严格）。
- 工具结果图片按「TOOL 文本结果 + 独立 USER 图片消息」建模，见 §6.1。

已实测验证：旧 `OpenAIMessage` JSON 可直接 `AgentMessage.model_validate_json()` 读回；
带附件消息经 `model_dump_json` / `model_validate_json` 往返不丢数据。

已知取舍：`from_openai()` 对多模态 `content` 只保留文本部分（丢弃响应里的图片块）。当前
流程中 LLM 响应都是文本 + tool_calls，多模态内容由本方通过 `attachments` 构造发出，因此
不影响现有场景；若将来需要保留响应里的图片，可把 content block 拆回 `attachments`。

### 5.1 存储字段

```python
message: AgentMessage | None = PydanticJsonField(AgentMessage, null=True)
```

DB 列仍为 TEXT，无迁移。

## 6. 转换边界

### 6.1 写入侧（只接受 AgentMessage）

`GtAgentHistory.build()`、Store 层（`agentHistoryStore.finalize_history_item()`、
`insert_compact_summary()`）只接受 `AgentMessage`，不兼容 `OpenAIMessage`。
`OpenAIMessage` 需由调用方先经 `AgentMessage.from_openai()` 转换后再传入。

工具结果含图片时，历史只存**一条** TOOL 消息——图片挂在 `attachments` 上（content 剥 base64）；
拆分与排序由 **llmService 发送时**统一完成（OpenAI 规范：`image_url` 只允许在 `user` 角色）：

```python
# 写入侧：一条 TOOL 消息，图片挂附件（AgentMessage.from_tool_result()）
final_message = AgentMessage.from_tool_result(tc.id, result_data)   # 图片结果 → TOOL + attachments
await self._history.finalize_history_item(output_item.id, message=final_message, ...)
```

发送侧（`llmService._split_tool_result_messages`）：带图片附件的 TOOL 消息拆成
「TOOL 文本 + USER 图片」两条，图片消息排在**所有** TOOL 结果之后（遇非 TOOL 消息或
列表末尾统一冲刷），避免多 tool_call 时图片插在工具结果中间：

```python
# 历史: [ASSISTANT(tool_calls=[A,B]), TOOL_A(image), TOOL_B]
# 发送: [ASSISTANT, TOOL_A(text), TOOL_B(text), USER(image)]
```

历史序列：`ASSISTANT(tool_calls) → TOOL×n(图片挂附件) → ASSISTANT(...)`，发送时展开为
`ASSISTANT(tool_calls) → TOOL×n(text) → USER(image) → ASSISTANT(...)`。

### 6.2 读取侧（统一转换出口）

- `GtAgentHistory.openai_message` → `self.message.to_openai_message()`
- `openai_message_or_none` → 有消息时转换，否则 `None`
- `GtAgentHistory.content` → `self.message.content`（`AgentMessage.content` 恒为 `str`，语义不变）

`build_infer_messages()` / `get_last_assistant_message()` / compact 的 `source_messages`
全部走 `openai_message`，无需改动。`llmService`、`compact.estimate_tokens`、
`GtCoreAgentDialogContext` 全程只见 `OpenAIMessage`，不感知变化。

## 7. 兼容性

- DB 列仍是 TEXT，旧 `OpenAIMessage` JSON 可直接读为 `AgentMessage`，无迁移。
- 无附件消息的 `to_openai_message()` 输出与改造前完全一致。
- 既有测试通过 `item.openai_message` / `item.content` 断言的行为保持不变。

## 8. 改动文件清单

| 文件 | 改动 |
|---|---|
| `src/util/llmApiUtil/OpenAiModels.py` | `content` 多模态 + `text_content()` + content block 类型 |
| `src/model/dbModel/agentMessage.py` | 新增独立类型 `AgentMessage` / `MessageAttachment` |
| `src/model/dbModel/gtAgentHistory.py` | `message` 字段类型、`build()` 包装、`content` / `openai_message*` 属性 |
| `src/service/agentService/agentHistoryStore.py` | 接受并包装两种类型 |
| `src/service/agentService/agentTurnRunner.py` | `text_content()` 守卫 + 工具结果图构造 `AgentMessage` |
| `src/dal/db/gtAgentHistoryManager.py` | 类型注解更新（逻辑不变） |
| `src/service/agentService/compact.py` | `text_content()` 守卫 |
| `src/controller/settingController.py` | `text_content()` 守卫 |

## 9. 已确认决策 / 后续事项

1. **TOOL 角色不允许 image_url（已定：方案 A）**：OpenAI 官方 schema 规定 `tool` 消息的
   content 数组只支持 `type: text`，`image_url` 仅允许在 `user` 角色（`system` / `developer`
   / `assistant` 同样只支持 text）。因此工具结果图片按「TOOL 文本结果 + 独立 USER 图片消息」
   建模（见 §6.1），`to_openai_message()` 只在 `user` 角色把附件转成 image_url。
2. **图片存储体积**：base64 内联进 DB 会显著膨胀表体积，大图建议用 `url` / 本地路径引用
   （如 `STORAGE_ROOT/workspace` 下的产物），仅持久化引用。
3. **token 估算**：多模态 content 的 `estimate_tokens` 走 litellm 原生估算（已验证支持）；
   字符兜底估算 `estimate_token_by_char` 已改为按 `text_content()` 计算。
