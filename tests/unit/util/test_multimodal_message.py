"""V25 多模态消息类型单元测试：OpenAIMessage content block / AgentMessage / 转换边界。"""
from __future__ import annotations

import pytest

from constants import OpenaiApiRole
from model.dbModel.agentMessage import AgentMessage, MessageAttachment
from model.dbModel.gtAgentHistory import GtAgentHistory
from util import llmApiUtil


# ─── OpenAIMessage 多模态 content ────────────────────────

def test_openai_message_text_content_roundtrip():
    """字符串 content 的序列化/反序列化往返不丢数据。"""
    msg = llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "hello")
    data = msg.model_dump_json()
    restored = llmApiUtil.OpenAIMessage.model_validate_json(data)
    assert restored.content == "hello"
    assert restored.text_content() == "hello"


def test_openai_message_multimodal_content_roundtrip():
    """多模态 content blocks 的序列化/反序列化往返不丢数据。"""
    msg = llmApiUtil.OpenAIMessage(
        role=OpenaiApiRole.USER,
        content=[
            llmApiUtil.OpenAITextContentBlock(text="look at this: "),
            llmApiUtil.OpenAIImageUrlContentBlock(image_url={"url": "data:image/png;base64,abc"}),
        ],
    )
    data = msg.model_dump_json()
    restored = llmApiUtil.OpenAIMessage.model_validate_json(data)
    assert restored.content is not None
    assert isinstance(restored.content, list)
    assert len(restored.content) == 2
    assert restored.text_content() == "look at this: "


def test_openai_message_text_content_for_blocks():
    """text_content() 对 blocks 数组拼接 text 块（忽略 image_url 块）。"""
    msg = llmApiUtil.OpenAIMessage(
        role=OpenaiApiRole.USER,
        content=[
            llmApiUtil.OpenAITextContentBlock(text="a"),
            llmApiUtil.OpenAIImageUrlContentBlock(image_url={"url": "data:image/png;base64,x"}),
            llmApiUtil.OpenAITextContentBlock(text="b"),
        ],
    )
    assert msg.text_content() == "ab"


def test_openai_message_text_content_for_str():
    """text_content() 对 str 原样返回。"""
    msg = llmApiUtil.OpenAIMessage.text(OpenaiApiRole.USER, "plain")
    assert msg.text_content() == "plain"


def test_openai_message_text_content_empty():
    """text_content() 对空 content 返回 None。"""
    msg = llmApiUtil.OpenAIMessage(role=OpenaiApiRole.USER, content=None)
    assert msg.text_content() is None


def test_openai_message_to_dict_multimodal():
    """to_dict() 对多模态 content 输出块数组。"""
    msg = llmApiUtil.OpenAIMessage(
        role=OpenaiApiRole.USER,
        content=[
            llmApiUtil.OpenAITextContentBlock(text="hi"),
            llmApiUtil.OpenAIImageUrlContentBlock(image_url={"url": "data:image/png;base64,zz"}),
        ],
    )
    d = msg.to_dict()
    assert d["content"] == [
        {"type": "text", "text": "hi"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,zz"}},
    ]


# ─── AgentMessage 兼容性 ─────────────────────────────────

def test_agent_message_parses_old_openai_json():
    """旧 OpenAIMessage JSON 可直接读为 AgentMessage（兼容）。"""
    old_json = '{"role": "user", "content": "hello"}'
    am = AgentMessage.model_validate_json(old_json)
    assert am.role == OpenaiApiRole.USER
    assert am.content == "hello"
    assert am.attachments is None


def test_agent_message_attachment_roundtrip():
    """带附件消息经 model_dump_json / model_validate_json 往返不丢附件。"""
    am = AgentMessage(
        role=OpenaiApiRole.USER,
        content="以下是工具执行结果的图片：",
        attachments=[MessageAttachment(kind="image", mime_type="image/png", data="iVBORw0KGgo=")],
    )
    data = am.model_dump_json()
    restored = AgentMessage.model_validate_json(data)
    assert restored.attachments is not None
    assert len(restored.attachments) == 1
    assert restored.attachments[0].mime_type == "image/png"
    assert restored.attachments[0].data == "iVBORw0KGgo="


# ─── AgentMessage.to_openai_message() 角色约束 ───────────

def test_to_openai_user_with_image_attachment():
    """user 角色 + 图片附件 → 拼 image_url data URL。"""
    am = AgentMessage(
        role=OpenaiApiRole.USER,
        content="看图",
        attachments=[MessageAttachment(kind="image", mime_type="image/png", data="AAAA")],
    )
    om = am.to_openai_message()
    assert om.content is not None
    assert isinstance(om.content, list)
    assert om.content[0].type == "text"
    assert om.content[1].type == "image_url"
    assert om.content[1].image_url["url"] == "data:image/png;base64,AAAA"


def test_to_openai_user_with_url_attachment_priority():
    """附件有 url 时优先使用 url，不拼 data URL。"""
    am = AgentMessage(
        role=OpenaiApiRole.USER,
        content="看图",
        attachments=[MessageAttachment(kind="image", mime_type="image/png", data="AAAA", url="file:///tmp/x.png")],
    )
    om = am.to_openai_message()
    blocks = [b for b in om.content if isinstance(b, llmApiUtil.OpenAIImageUrlContentBlock)]
    assert blocks[0].image_url["url"] == "file:///tmp/x.png"


def test_to_openai_tool_role_ignores_attachment():
    """tool 角色即使有附件也不产出 image_url（OpenAI 规范）。"""
    am = AgentMessage(
        role=OpenaiApiRole.TOOL,
        content='{"file_path": "/tmp/a.png", "mime_type": "image/png"}',
        tool_call_id="tc-1",
        attachments=[MessageAttachment(kind="image", mime_type="image/png", data="AAAA")],
    )
    om = am.to_openai_message()
    assert om.content == '{"file_path": "/tmp/a.png", "mime_type": "image/png"}'
    assert om.tool_call_id == "tc-1"


def test_to_openai_no_attachment_output_same():
    """无附件消息 to_openai_message() 输出与改造前一致。"""
    am = AgentMessage(role=OpenaiApiRole.USER, content="hello")
    om = am.to_openai_message()
    assert om.role == OpenaiApiRole.USER
    assert om.content == "hello"
    assert om.tool_calls is None
    assert om.tool_call_id is None


# ─── GtAgentHistory 构建（只接受 AgentMessage） ─────────────

def test_gt_agent_history_build_accepts_agent_message():
    """GtAgentHistory.build() 只接受 AgentMessage，OpenAIMessage 需先经 from_openai() 转换。"""
    om = llmApiUtil.OpenAIMessage(
        role=OpenaiApiRole.ASSISTANT,
        content="ok",
        tool_calls=[llmApiUtil.OpenAIToolCall(id="tc-1", function={"name": "x", "arguments": "{}"})],
    )
    item = GtAgentHistory.build(AgentMessage.from_openai(om))
    assert isinstance(item.message, AgentMessage)
    assert item.role == OpenaiApiRole.ASSISTANT
    assert item.message.content == "ok"
    assert item.message.tool_calls is not None
    assert item.message.tool_calls[0].id == "tc-1"
    # openai_message 属性返回转换后的 OpenAIMessage
    assert item.openai_message.role == OpenaiApiRole.ASSISTANT
    assert item.openai_message.content == "ok"


def test_gt_agent_history_content_and_tool_calls_semantics():
    """既有 content / tool_calls 访问语义不变。"""
    am = AgentMessage(role=OpenaiApiRole.ASSISTANT, content="reply")
    item = GtAgentHistory.build(am)
    assert item.content == "reply"
    assert item.tool_calls is None


def test_gt_agent_history_accepts_agent_message_directly():
    """GtAgentHistory.build(AgentMessage) 直接使用，不二次包装。"""
    am = AgentMessage(
        role=OpenaiApiRole.USER,
        content="看图",
        attachments=[MessageAttachment(kind="image", mime_type="image/png", data="QQ")],
    )
    item = GtAgentHistory.build(am)
    assert item.message is am
    assert item.message.attachments is not None


def test_gt_agent_history_openai_message_multimodal():
    """带附件历史消息的 openai_message 属性输出多模态 OpenAIMessage。"""
    am = AgentMessage(
        role=OpenaiApiRole.USER,
        content="看图",
        attachments=[MessageAttachment(kind="image", mime_type="image/jpeg", data="JJ")],
    )
    item = GtAgentHistory.build(am)
    om = item.openai_message
    blocks = [b for b in om.content if isinstance(b, llmApiUtil.OpenAIImageUrlContentBlock)]
    assert len(blocks) == 1
    assert blocks[0].image_url["url"] == "data:image/jpeg;base64,JJ"


# ─── MessageAttachment.from_tool_result 图片识别 ──────────

def test_from_tool_result_detects_image():
    """含 mime_type(image/*) + base64 的结果识别为图片附件。"""
    att = MessageAttachment.from_tool_result({"mime_type": "image/png", "base64": "QQ==", "format": "png"})
    assert att is not None
    assert att.kind == "image"
    assert att.mime_type == "image/png"
    assert att.data == "QQ=="


def test_from_tool_result_rejects_non_image():
    """非图片 shape 返回 None：缺 base64、缺 mime、mime 非 image/*、非 dict。"""
    assert MessageAttachment.from_tool_result({"mime_type": "image/png"}) is None
    assert MessageAttachment.from_tool_result({"base64": "QQ=="}) is None
    assert MessageAttachment.from_tool_result({"mime_type": "text/plain", "base64": "QQ=="}) is None
    assert MessageAttachment.from_tool_result({"mime_type": "image/png", "base64": ""}) is None
    assert MessageAttachment.from_tool_result({"success": False, "message": "boom"}) is None
    assert MessageAttachment.from_tool_result(None) is None
    assert MessageAttachment.from_tool_result("not a dict") is None


# ─── AgentMessage.from_tool_result 工具结果 → 单条历史消息 ──

def test_from_tool_result_image_attached_to_tool_message():
    """图片结果：单条 TOOL 消息，content 剥 base64，图片挂附件。"""
    tool_msg = AgentMessage.from_tool_result("tc-1", {
        "success": True,
        "mime_type": "image/png",
        "base64": "QQ==",
        "file_path": "/tmp/x.png",
    })
    assert tool_msg.role == OpenaiApiRole.TOOL
    assert tool_msg.tool_call_id == "tc-1"
    # content 剥离 base64，保留元数据
    assert "base64" not in tool_msg.content
    assert '"format"' not in tool_msg.content
    assert "image/png" in tool_msg.content
    # 图片挂附件，含 caption（供 llmService 拆出 USER 图片描述）
    assert tool_msg.attachments is not None
    assert tool_msg.attachments[0].kind == "image"
    assert tool_msg.attachments[0].mime_type == "image/png"
    assert tool_msg.attachments[0].data == "QQ=="
    assert "以下是工具执行结果的图片（/tmp/x.png）" in tool_msg.attachments[0].caption
    # TOOL 角色 to_openai_message() 忽略附件，只出文本（图片拆分由 llmService 负责）
    om = tool_msg.to_openai_message()
    assert om.role == OpenaiApiRole.TOOL
    assert isinstance(om.content, str)
    assert "base64" not in om.content


def test_from_tool_result_plain_result_single_message():
    """非图片结果：单条 TOOL 消息，无附件。"""
    tool_msg = AgentMessage.from_tool_result("tc-1", {"success": True, "content": "demo"})
    assert tool_msg.role == OpenaiApiRole.TOOL
    assert tool_msg.tool_call_id == "tc-1"
    assert '"content": "demo"' in tool_msg.content
    assert tool_msg.attachments is None
