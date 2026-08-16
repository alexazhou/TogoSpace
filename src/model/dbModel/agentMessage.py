"""AgentMessage — 历史消息自定义存储类型。

独立于 `llmApiUtil.OpenAIMessage`：持久化 openai 语义字段 + 附件（图片等）。
发送时通过 `to_openai_message()` 转回 `OpenAIMessage`。

背景：V25 引入多模态。`OpenAIMessage.content` 支持多模态 content block，
但存储侧保持朴素（`content` 恒为 str），多模态只存在于附件与转换边界。
"""
from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from constants import OpenaiApiRole
from util import llmApiUtil


class MessageAttachment(BaseModel):
    """消息附件（如图片工具结果）。"""

    kind: Literal["image", "file", "text"] = "image"
    mime_type: str | None = None     # image/png
    data: str | None = None          # base64 内联数据
    url: str | None = None           # 或引用路径 / URL（大图推荐，避免撑爆 DB）
    caption: str | None = None       # 说明文字，发送时可转成文本块

    @classmethod
    def from_tool_result(cls, result_data: dict | None) -> "MessageAttachment | None":
        """从工具结果 dict 识别图片附件；shape 不匹配（非图片）返回 None。

        识别规则：结果为 dict 且含 `mime_type`（`image/*`）与非空 `base64` 即视为图片结果。
        """
        if not isinstance(result_data, dict):
            return None
        mime = result_data.get("mime_type")
        base64 = result_data.get("base64")
        if not (isinstance(mime, str) and mime.startswith("image/") and isinstance(base64, str) and base64):
            return None
        return cls(kind="image", mime_type=mime, data=base64)


class AgentMessage(BaseModel):
    """历史消息自定义类型：独立于 OpenAIMessage，持久化 openai 语义字段 + 附件。"""

    role: OpenaiApiRole
    content: str | None = None                            # 纯文本部分（发给 LLM）
    reasoning_content: str | None = None
    tool_calls: list[llmApiUtil.OpenAIToolCall] | None = None
    tool_call_id: str | None = None
    attachments: list[MessageAttachment] | None = None     # 图片/文件等放不进 content 的内容

    @classmethod
    def tool_result(cls, tool_call_id: str, result: str) -> "AgentMessage":
        """构造工具调用结果消息（role=TOOL）。"""
        return cls(
            role=OpenaiApiRole.TOOL,
            content=result,
            tool_call_id=tool_call_id,
        )

    @classmethod
    def from_tool_result(
        cls,
        tool_call_id: str,
        result: dict,
    ) -> "AgentMessage":
        """把工具执行结果转成一条 TOOL 历史消息。

        结果为图片（shape 判断见 `MessageAttachment.from_tool_result`）时：
        - content 剥离 base64 只留元数据
        - 图片挂在 `attachments` 上（含 caption），发送时由 llmService 拆成
          「TOOL 文本 + USER 图片」两条 OpenAIMessage（OpenAI 规范：image_url 仅允许在 user 角色）
        非图片结果：普通 TOOL 消息，无附件。
        """
        attachment = MessageAttachment.from_tool_result(result)
        if attachment is not None:
            lean_result = {k: v for k, v in result.items() if k != "base64"}
            image_desc = result.get("file_path") or "工具执行结果图片"
            attachment.caption = f"以下是工具执行结果的图片（{image_desc}）："
            return cls(
                role=OpenaiApiRole.TOOL,
                content=json.dumps(lean_result, ensure_ascii=False),
                tool_call_id=tool_call_id,
                attachments=[attachment],
            )
        return cls.tool_result(tool_call_id, json.dumps(result, ensure_ascii=False))

    @classmethod
    def from_openai(cls, msg: llmApiUtil.OpenAIMessage) -> "AgentMessage":
        """把 OpenAIMessage 转为 AgentMessage（多模态 content 只保留文本部分）。"""
        return cls(
            role=msg.role,
            content=msg.text_content(),
            reasoning_content=msg.reasoning_content,
            tool_calls=msg.tool_calls,
            tool_call_id=msg.tool_call_id,
        )

    def to_openai_message(self) -> llmApiUtil.OpenAIMessage:
        """发送时转换：user 角色且有附件时拼多模态 content blocks；其余角色保持纯文本。

        OpenAI 规范限制：`image_url` 只允许出现在 `user` 角色的 content 里，
        `tool` / `system` / `assistant` 角色的 content 只支持 text。
        因此附件仅在 `user` 角色生效，其他角色忽略附件。
        """
        content: str | list[llmApiUtil.OpenAIContentBlock] | None = self.content
        if self.role == OpenaiApiRole.USER and self.attachments:
            blocks: list[llmApiUtil.OpenAIContentBlock] = []
            if self.content:
                blocks.append(llmApiUtil.OpenAITextContentBlock(text=self.content))
            for att in self.attachments:
                if att.kind == "image":
                    url = att.url or f"data:{att.mime_type or 'image/png'};base64,{att.data}"
                    blocks.append(llmApiUtil.OpenAIImageUrlContentBlock(image_url={"url": url}))
                elif att.kind == "text" and att.caption:
                    blocks.append(llmApiUtil.OpenAITextContentBlock(text=att.caption))
            content = blocks or None
        return llmApiUtil.OpenAIMessage(
            role=self.role,
            content=content,
            reasoning_content=self.reasoning_content,
            tool_calls=self.tool_calls,
            tool_call_id=self.tool_call_id,
        )
