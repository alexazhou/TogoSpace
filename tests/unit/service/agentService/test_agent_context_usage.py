"""`contextUsage` 单元测试：分母口径、分子来源、阈值判据、广播。

这一组用例重点守四件事，每件都刻意设计成「假如实现写错了会红」：

1. **分母是 hard limit，不是模型窗口**。用 131072 / 16384 这组默认值断言
   `limit_tokens == 114688`；若有人把分母换回 `context_window_tokens`，
   第一个用例立刻红（131072 != 114688）。
2. **阈值来自后端配置，不是硬编码 0.85**。把 `compact_trigger_ratio` 改成 0.5，
   断言 `compact_trigger_tokens == floor(limit * 0.5)`。若实现里写死 0.85，
   这个用例红。
3. **`used_tokens` 传了就优先用**，不去跑 tokenizer；没传才算。
4. **`ratio` 不 clamp**：超限时 `ratio > 1`，把「已经超出容量」这个诊断信息留住。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from model.coreModel.agentModel import AgentContextUsage
from model.dbModel.agentMessage import AgentMessage
from constants import OpenaiApiRole
from service.agentService import compact, contextUsage
from util.configUtil.configTypes import LlmContextConfig, LlmModelConfig


def _make_llm_config(**ctx_overrides) -> LlmModelConfig:
    """构造一份 context_config 受控的模型配置。"""
    ctx = {
        "context_window_tokens": 131072,
        "reserve_output_tokens": 16384,
        "compact_trigger_ratio": 0.85,
        "compact_summary_max_tokens": 6144,
    }
    ctx.update(ctx_overrides)
    return LlmModelConfig(
        name="qwen-plus",
        protocol="openai",
        context_config=LlmContextConfig(**ctx),
    )


def _patch_model(cfg: LlmModelConfig):
    """把 `resolve_model` 与 `configUtil.get_app_config` 一起打桩。

    两处补丁都必须在**使用方模块**里打：
    - `contextUsage.resolve_model`：`contextUsage` 用 `from ... import resolve_model`
      引入，打定义处 `llmService.core` 不生效；
    - `compact.configUtil.get_app_config`：`calc_hard_limit_tokens` 每次都要读全局
      provider 配置，单测没有 `configUtil.load()` 过，不打桩会抛
      `AppConfig 未初始化`。这与既有 `test_compact_policy.py` 的做法一致。

    全局配置这里给「用户没改过」的默认 `LlmContextConfig()`，让 `cfg` 自己的
    context_config 成为唯一变量；否则全局值会参与合并，用例的预期就不可控了。
    """
    app_config = MagicMock()
    app_config.setting.context_config = LlmContextConfig()

    class _Both:
        def __enter__(self):
            self._p1 = patch.object(contextUsage, "resolve_model", return_value=(MagicMock(), cfg))
            self._p2 = patch.object(compact.configUtil, "get_app_config", return_value=app_config)
            self._p1.start()
            self._p2.start()
            return self

        def __exit__(self, *exc):
            self._p2.stop()
            self._p1.stop()
            return False

    return _Both()


# ─── 分母口径 ────────────────────────────────────────────


def test_limit_tokens_is_hard_limit_not_context_window() -> None:
    """分母 = context_window − reserve_output，**不是** context_window。

    131072 − 16384 = 114688。若实现把分母写成 `context_window_tokens`，
    这里会得到 131072 而红。
    """
    cfg = _make_llm_config()
    with _patch_model(cfg):
        usage = contextUsage.calc_context_usage(
            model_name="qwen-plus@test", messages=[], system_prompt=None, used_tokens=1000,
        )
    assert usage is not None
    assert usage.limit_tokens == 114688
    assert usage.context_window_tokens == 131072  # 原始窗口保留，仅供展示
    assert usage.limit_tokens != usage.context_window_tokens


def test_ratio_uses_limit_tokens_as_denominator() -> None:
    """`ratio` 必须用 hard limit 做分母：97484 / 114688 ≈ 0.85，不是 0.74。"""
    cfg = _make_llm_config()
    with _patch_model(cfg):
        usage = contextUsage.calc_context_usage(
            model_name="qwen-plus@test", messages=[], system_prompt=None, used_tokens=97484,
        )
    assert usage is not None
    # 用 limit 做分母 → 接近 0.85（红区临界）；用窗口做分母会是 0.7437
    assert usage.ratio == 97484 / 114688
    assert usage.ratio > 0.8499
    assert usage.ratio < 0.85


# ─── 阈值来源 ────────────────────────────────────────────


def test_trigger_follows_configured_ratio_not_hardcoded_default() -> None:
    """把 ratio 改成 0.5，触发线必须跟着变——证明阈值是读配置的。

    若实现里把 0.85 写死，这里会得到 97484 而不是 57344，红。
    """
    cfg = _make_llm_config(compact_trigger_ratio=0.5)
    with _patch_model(cfg):
        usage = contextUsage.calc_context_usage(
            model_name="qwen-plus@test", messages=[], system_prompt=None, used_tokens=0,
        )
    assert usage is not None
    assert usage.compact_trigger_tokens == 57344  # floor(114688 * 0.5)
    assert usage.compact_trigger_tokens == int(usage.limit_tokens * 0.5)


def test_trigger_uses_floor_so_it_never_exceeds_the_limit() -> None:
    """`floor` 语义：`used = trigger` 时 ratio 严格小于配置的 ratio。

    这正是「红区为什么几乎是瞬态」的算术根源，用用例把这个事实钉住：
    `floor(114688 * 0.85) = 97484`，而 `97484 / 114688 = 0.849993 < 0.85`。
    """
    cfg = _make_llm_config()
    with _patch_model(cfg):
        usage = contextUsage.calc_context_usage(
            model_name="qwen-plus@test", messages=[], system_prompt=None, used_tokens=97484,
        )
    assert usage is not None
    assert usage.compact_trigger_tokens == 97484
    assert usage.ratio < 0.85  # 已达触发线，但比例仍 < 0.85 —— 1 token 窗口


# ─── 分子来源 ────────────────────────────────────────────


def test_explicit_used_tokens_skips_estimation() -> None:
    """给了 `used_tokens` 就不该再跑 tokenizer（推理收尾已拿到真实值）。"""
    cfg = _make_llm_config()
    with _patch_model(cfg), patch.object(compact, "estimate_tokens") as mock_est:
        usage = contextUsage.calc_context_usage(
            model_name="qwen-plus@test", messages=[], system_prompt="s", used_tokens=42000,
        )
    assert usage is not None
    assert usage.used_tokens == 42000
    mock_est.assert_not_called()


def test_missing_used_tokens_falls_back_to_estimation() -> None:
    """没给就用 `compact.estimate_tokens`，system_prompt 一起传进去。"""
    cfg = _make_llm_config()
    with _patch_model(cfg), patch.object(compact, "estimate_tokens", return_value=1234) as mock_est:
        usage = contextUsage.calc_context_usage(
            model_name="qwen-plus@test",
            messages=[AgentMessage(role=OpenaiApiRole.USER, content="hi")],
            system_prompt="SYS",
        )
    assert usage is not None
    assert usage.used_tokens == 1234
    mock_est.assert_called_once()
    # 第三个位置参数是 system_prompt：漏传会让分子少算整段系统提示词
    assert mock_est.call_args.args[2] == "SYS"


# ─── ratio 不 clamp ──────────────────────────────────────


def test_ratio_not_clamped_when_over_limit() -> None:
    """超限时 `ratio > 1`，不在这里压成 1.0。

    超限是真实可发生的（估算值 vs 配置值口径不同）。压掉它，前端就只能显示
    100% 而无从知道「其实已经超了」。clamp 留给渲染层。
    """
    cfg = _make_llm_config()
    with _patch_model(cfg):
        usage = contextUsage.calc_context_usage(
            model_name="qwen-plus@test", messages=[], system_prompt=None, used_tokens=200000,
        )
    assert usage is not None
    assert usage.ratio > 1.0
    assert usage.ratio == 200000 / 114688


# ─── 算不出来时返回 None，而不是 0 ────────────────────────


def test_returns_none_when_model_config_unresolvable() -> None:
    """模型配置解析失败 → `None`（前端整条不渲染），不是「用量 0」。"""
    with patch.object(contextUsage, "resolve_model", side_effect=ValueError("未配置")):
        usage = contextUsage.calc_context_usage(
            model_name="", messages=[], system_prompt=None, used_tokens=1,
        )
    assert usage is None


def test_returns_none_when_limit_is_not_positive() -> None:
    """`reserve_output` 配得比窗口还大 → 分母非正 → `None`，不造除零/负比例。"""
    cfg = _make_llm_config(context_window_tokens=1000, reserve_output_tokens=2000)
    with _patch_model(cfg):
        usage = contextUsage.calc_context_usage(
            model_name="qwen-plus@test", messages=[], system_prompt=None, used_tokens=10,
        )
    assert usage is None


# ─── 从历史行计算 ────────────────────────────────────────


class _FakeHistoryItem:
    """只带 `message` 的最小历史行替身。"""

    def __init__(self, message: AgentMessage | None) -> None:
        self.message = message


def test_history_items_without_message_are_skipped() -> None:
    """pending infer 占位行（`message is None`）不计入分子。

    把它们算进去会让用量虚高，而它们此刻还没有任何文本内容。
    """
    cfg = _make_llm_config()
    items = [
        _FakeHistoryItem(AgentMessage(role=OpenaiApiRole.USER, content="a")),
        _FakeHistoryItem(None),  # pending 占位
        _FakeHistoryItem(AgentMessage(role=OpenaiApiRole.ASSISTANT, content="b")),
    ]
    with _patch_model(cfg), patch.object(compact, "estimate_tokens", return_value=99) as mock_est:
        usage = contextUsage.calc_context_usage_from_history(
            model_name="qwen-plus@test", items=items, system_prompt="SYS",
        )
    assert usage is not None
    assert usage.used_tokens == 99
    passed_messages = mock_est.call_args.args[1]
    assert len(passed_messages) == 2


# ─── 广播 ────────────────────────────────────────────────


def test_publish_skips_negative_agent_id() -> None:
    """Operator / SYSTEM（负 id）不广播：前端本来就不渲染它们。"""
    usage = AgentContextUsage(
        used_tokens=1, limit_tokens=10, compact_trigger_tokens=8,
        context_window_tokens=20, ratio=0.1,
    )
    with patch.object(contextUsage.messageBus, "publish") as mock_publish:
        contextUsage.publish_context_usage(team_id=1, agent_id=-1, usage=usage)
    mock_publish.assert_not_called()


def test_publish_skips_none_usage() -> None:
    """没有数值就不广播：否则前端会收到一帧 null 把已有数值抹掉。"""
    with patch.object(contextUsage.messageBus, "publish") as mock_publish:
        contextUsage.publish_context_usage(team_id=1, agent_id=10, usage=None)
    mock_publish.assert_not_called()


def test_publish_emits_topic_and_serialized_payload() -> None:
    """正常广播：topic 正确，payload 是可直接下发前端的 dict（不是模型对象）。"""
    usage = AgentContextUsage(
        used_tokens=100, limit_tokens=1000, compact_trigger_tokens=850,
        context_window_tokens=1200, ratio=0.1,
    )
    with patch.object(contextUsage.messageBus, "publish") as mock_publish:
        contextUsage.publish_context_usage(team_id=2, agent_id=7, usage=usage)

    mock_publish.assert_called_once()
    args, kwargs = mock_publish.call_args
    assert args[0].name == "AGENT_CONTEXT_USAGE_CHANGED"
    assert kwargs["team_id"] == 2
    assert kwargs["agent_id"] == 7
    payload = kwargs["context_usage"]
    assert isinstance(payload, dict)
    assert payload["used_tokens"] == 100
    assert payload["limit_tokens"] == 1000
    assert payload["compact_trigger_tokens"] == 850
    assert payload["context_window_tokens"] == 1200
    assert payload["ratio"] == 0.1
