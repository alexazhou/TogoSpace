"""visionFallback.recognize 单测：单图识别返回文本、失败抛异常、截断。"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from model.dbModel.agentMessage import MessageAttachment
from service.agentService import visionFallback
from service.llmService.core import InferResult
from util import llmApiUtil


def _build_response(content: str) -> llmApiUtil.OpenAIResponse:
    return llmApiUtil.OpenAIResponse.model_validate({
        "id": "resp_vision",
        "object": "chat.completion",
        "created": 1710000000,
        "model": "vision-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    })


@pytest.mark.asyncio
async def test_recognize_returns_text_description():
    """单图识别：返回视觉模型的纯文本描述。"""
    att = MessageAttachment(kind="image", mime_type="image/png", data="QQ==")

    with (
        patch.object(visionFallback, "resolve_model", return_value=(MagicMock(), MagicMock())) as mock_resolve,
        patch.object(visionFallback.llmService, "infer", AsyncMock(return_value=InferResult.success(_build_response("图中是一只猫")))),
    ):
        text = await visionFallback.recognize(att)

    assert text == "图中是一只猫"
    mock_resolve.assert_called_once_with("vision@system")


@pytest.mark.asyncio
async def test_recognize_raises_when_vision_slot_unconfigured():
    """vision@system 未配置（resolve_model 抛 ValueError）→ 异常向上抛出，由调用方降级。"""
    att = MessageAttachment(kind="image", mime_type="image/png", data="QQ==")

    with (
        patch.object(visionFallback, "resolve_model", side_effect=ValueError("未配置有效的系统槽位：vision")),
        patch.object(visionFallback.llmService, "infer", AsyncMock()),
    ):
        with pytest.raises(ValueError):
            await visionFallback.recognize(att)


@pytest.mark.asyncio
async def test_recognize_raises_when_infer_failed():
    """视觉模型 infer 返回失败 → 抛异常，由调用方降级。"""
    att = MessageAttachment(kind="image", mime_type="image/png", data="QQ==")

    with (
        patch.object(visionFallback, "resolve_model", return_value=(MagicMock(), MagicMock())),
        patch.object(visionFallback.llmService, "infer", AsyncMock(return_value=InferResult.failure(RuntimeError("timeout")))),
    ):
        with pytest.raises(RuntimeError, match="timeout"):
            await visionFallback.recognize(att)


@pytest.mark.asyncio
async def test_recognize_raises_when_empty_text():
    """识别返回空文本 → 抛异常。"""
    att = MessageAttachment(kind="image", mime_type="image/png", data="QQ==")

    with (
        patch.object(visionFallback, "resolve_model", return_value=(MagicMock(), MagicMock())),
        patch.object(visionFallback.llmService, "infer", AsyncMock(return_value=InferResult.success(_build_response("   ")))),
    ):
        with pytest.raises(RuntimeError, match="empty text"):
            await visionFallback.recognize(att)


@pytest.mark.asyncio
async def test_recognize_truncates_long_text():
    """识别结果超过 500 字被截断。"""
    long_text = "图" * 600
    att = MessageAttachment(kind="image", mime_type="image/png", data="QQ==")

    with (
        patch.object(visionFallback, "resolve_model", return_value=(MagicMock(), MagicMock())),
        patch.object(visionFallback.llmService, "infer", AsyncMock(return_value=InferResult.success(_build_response(long_text)))),
    ):
        text = await visionFallback.recognize(att)

    assert len(text) <= visionFallback._VISION_CAPTION_MAX_CHARS + len("...(已截断)")
    assert text.endswith("...(已截断)")


def test_truncate_caption_short_unchanged():
    """短文本不截断。"""
    assert visionFallback._truncate_caption("  你好  ") == "你好"
