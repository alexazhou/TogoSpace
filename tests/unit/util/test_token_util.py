"""tokenUtil 单元测试：流式增量的本地 token 估算。"""
from unittest.mock import patch

from util import tokenUtil


def test_estimate_empty_text_returns_zero():
    assert tokenUtil.estimate_text_tokens("gpt-4o", "") == 0
    assert tokenUtil.estimate_text_tokens("gpt-4o", None) == 0


def test_estimate_text_uses_litellm_token_counter():
    with patch.object(tokenUtil.litellm, "token_counter", return_value=7) as counter:
        assert tokenUtil.estimate_text_tokens("mock-model", "hello world") == 7
    counter.assert_called_once_with(model="mock-model", text="hello world")


def test_estimate_text_falls_back_to_char_estimate_on_error():
    with patch.object(tokenUtil.litellm, "token_counter", side_effect=RuntimeError("boom")):
        assert tokenUtil.estimate_text_tokens("mock-model", "abcdefgh") == 2


def test_estimate_text_fallback_never_returns_zero_for_non_empty_text():
    with patch.object(tokenUtil.litellm, "token_counter", side_effect=RuntimeError("boom")):
        assert tokenUtil.estimate_text_tokens("mock-model", "ab") == 1
