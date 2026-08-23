"""视觉模型 Fallback：用视觉模型识别单张图片，返回文本描述。

职责边界（V26 设计文档 step2）：
- 本模块只负责「识别」：`recognize(att)` 接收一张图片附件，调用视觉模型识别并返回纯文本描述。
  循环遍历消息、写回 caption、显式持久化由调用方（agentTurnRunner）负责。
- llmService 负责「插入」：转换层按模型能力 + caption 有无决定最终消息形态（发图 / caption 文本 / 说明文本）。

识别失败（超时 / 报错 / vision@system 未配置）由本模块抛出异常，由调用方捕获降级：
caption 保持为空，由 llmService 转换层插入"无法读取图片"说明，不阻塞 agent 主流程。
"""
from __future__ import annotations

import logging

from constants import OpenaiApiRole
from model.coreModel.gtCoreChatModel import GtCoreAgentDialogContext
from model.dbModel.agentMessage import AgentMessage, MessageAttachment
from service import llmService
from service.agentService.prompts import VISION_RECOGNIZE_PROMPT
from service.llmService.core import resolve_model

logger = logging.getLogger(__name__)

_VISION_CAPTION_MAX_CHARS = 500  # 识别结果截断，避免污染主模型上下文
_VISION_SYSTEM_PROMPT = "你是一个图片识别助手，请根据图片内容回答。"


def _truncate_caption(text: str) -> str:
    """截断识别结果到约 500 字摘要。"""
    text = (text or "").strip()
    if len(text) <= _VISION_CAPTION_MAX_CHARS:
        return text
    return text[:_VISION_CAPTION_MAX_CHARS].rstrip() + "...(已截断)"


async def recognize(att: MessageAttachment) -> str:
    """用视觉模型识别单张图片，返回纯文本描述。失败抛异常由调用方捕获降级。"""
    # resolve_model("vision@system") 未配置时抛 ValueError，由调用方捕获降级
    resolve_model("vision@system")

    recog_msg = AgentMessage(
        role=OpenaiApiRole.USER,
        content=VISION_RECOGNIZE_PROMPT,
        attachments=[att],
    )
    ctx = GtCoreAgentDialogContext(
        system_prompt=_VISION_SYSTEM_PROMPT,
        messages=[recog_msg],
    )
    result = await llmService.infer("vision@system", ctx)
    if not result.ok or result.response is None:
        raise RuntimeError(result.error_message or "vision recognize failed")
    text = (result.response.choices[0].message.content or "").strip()
    if not text:
        raise RuntimeError("vision recognize returned empty text")
    return _truncate_caption(text)
