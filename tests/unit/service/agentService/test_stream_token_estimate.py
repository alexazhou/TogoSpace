"""流式 token 估算的核心口径判据（ST2 / ST3 / ST4 / ST5 / ST7）。

这些用例直接驱动 `llmService.infer_stream`：把 `llmApiUtil.send_request_stream`
换成按脚本投递 chunk 的假实现，从而精确控制每个 delta 的三路内容
（content / reasoning_content / tool_calls.arguments）以及是否携带 usage。

设计原则：断言只用「可数的数字」（tokenize 次数、文本总长、回调次数），
不用「跑得挺快」这类不可复现的证据。
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from model.coreModel.gtCoreChatModel import GtCoreAgentDialogContext
from model.dbModel.agentMessage import AgentMessage
from service import llmService
from service.agentService import agentTurnRunner as runner_module
from util import configUtil, llmApiUtil
from util.configUtil.configTypes import AppConfig, SettingConfig

# 节流参数从被测模块取，不写死。旧版本（无进度回调）没有这两个常量——
# 用 getattr 兜住，让红出现在用例断言处而不是 import 阶段，
# 这样才能看出「被咬的是行为」而不是「模块缺了个名字」。
_PROGRESS_THROTTLE_CHUNK_COUNT = getattr(runner_module, "_PROGRESS_THROTTLE_CHUNK_COUNT", 10)
_PROGRESS_THROTTLE_INTERVAL_SECONDS = getattr(runner_module, "_PROGRESS_THROTTLE_INTERVAL_SECONDS", 0.2)
AgentTurnRunner = runner_module.AgentTurnRunner


# ── 夹具：配置 / ctx / chunk 脚本 / 假传输层 ──────────────────────

def _install_config(monkeypatch) -> None:
    monkeypatch.setattr(configUtil, "get_app_config", lambda: AppConfig(setting=SettingConfig(
        default_models={"primary": "mock-model@svc"},
        llm_providers=[
            {
                "name": "svc",
                "type": "openai",
                "urls": {"openai": "http://localhost/v1/chat/completions"},
                "api_key": "key-123",
                "models": [{"name": "mock-model", "protocol": "openai"}],
            }
        ],
    )))


def _ctx() -> GtCoreAgentDialogContext:
    return GtCoreAgentDialogContext(
        system_prompt="system prompt",
        session_id="session-stream",
        messages=[AgentMessage(role=llmApiUtil.OpenaiApiRole.USER, content="hello")],
        tool_choice="none",
    )


def _build_response(content: str = "ok") -> llmApiUtil.OpenAIResponse:
    return llmApiUtil.OpenAIResponse.model_validate({
        "id": "resp_stream",
        "object": "chat.completion",
        "created": 1710000000,
        "model": "mock-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    })


def _chunk(*, content=None, reasoning=None, tool_args=None, usage=None):
    """构造一个流式 chunk。

    `core._on_chunk` 只用 getattr 读取，所以 SimpleNamespace 足够，
    不需要构造 litellm 的 ModelResponseStream。
    """
    delta = SimpleNamespace(content=content, reasoning_content=reasoning, tool_calls=None)
    if tool_args is not None:
        delta.tool_calls = [SimpleNamespace(function=SimpleNamespace(arguments=tool_args))]
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)], usage=usage)


def _install_transport(monkeypatch, chunks: list) -> list:
    """把 send_request_stream 换成按脚本投递 chunk 的假实现，返回已投递列表。"""
    delivered: list = []

    async def _fake_send_request_stream(
        request, url, api_key, custom_llm_provider=None,
        extra_headers=None, on_chunk=None, request_id="",
    ):
        for chunk in chunks:
            delivered.append(chunk)
            if on_chunk is not None:
                result = on_chunk(chunk)
                if hasattr(result, "__await__"):
                    await result
        return _build_response()

    monkeypatch.setattr(llmService.llmApiUtil, "send_request_stream", _fake_send_request_stream)
    return delivered


class _ProgressRecorder:
    """同时支持同步/异步调用的 on_progress 记录器。"""

    def __init__(self):
        self.items: list = []

    async def __call__(self, progress) -> None:
        self.items.append(progress)


async def _run_stream(monkeypatch, chunks: list):
    _install_config(monkeypatch)
    _install_transport(monkeypatch, chunks)
    recorder = _ProgressRecorder()
    result = await llmService.infer_stream(None, _ctx(), on_progress=recorder)
    assert result.ok is True, result.error_message
    return recorder


# ── ST2：三路 delta 都计入 completion_tokens ──────────────────────

_ST2_WEIGHTS = {"abc": 3, "思考过程": 4, '{"cmd":"ls"}': 5}
_ST2_TOTAL = sum(_ST2_WEIGHTS.values())


@pytest.mark.asyncio
async def test_st2_content_reasoning_and_tool_arguments_all_counted(monkeypatch):
    """content / reasoning_content / tool_calls.arguments 三路都必须计入。"""
    seen: list[str] = []

    def _fake_estimate(model, text):
        seen.append(text)
        return _ST2_WEIGHTS.get(text, 0)

    monkeypatch.setattr("service.llmService.core.tokenUtil.estimate_text_tokens", _fake_estimate)

    recorder = await _run_stream(monkeypatch, [
        _chunk(content="abc"),
        _chunk(reasoning="思考过程"),
        _chunk(tool_args='{"cmd":"ls"}'),
    ])

    assert recorder.items[-1].estimated_completion_tokens == _ST2_TOTAL
    # 三路各被 tokenize 一次：缺任何一路都会让 totals / seen 对不上
    assert sorted(seen) == sorted(_ST2_WEIGHTS.keys())
    # 生产者侧的负向约束：估算分支**不得**顺手把估算值填进真值键。
    # 少了这一条，`core.py` 估算分支里加一行 `completion_tokens=completion_tokens`
    # 全量测试一条都不会红（M3 断的是「落库」、M5-a 断的是消费者 `apply_progress`，
    # 都不经过这个生产者）。前端的「估算/真值」着色完全依赖这个键的缺席。
    assert recorder.items[-1].completion_tokens is None


@pytest.mark.asyncio
async def test_st2_reasoning_only_delta_contributes_nonzero(monkeypatch):
    """思考模型只发 reasoning_content 时，估算不能一直是 0。"""
    monkeypatch.setattr(
        "service.llmService.core.tokenUtil.estimate_text_tokens",
        lambda model, text: len(text),
    )

    recorder = await _run_stream(monkeypatch, [
        _chunk(reasoning="思"),
        _chunk(reasoning="考"),
    ])

    assert recorder.items[0].estimated_completion_tokens == 1
    assert recorder.items[-1].estimated_completion_tokens == 2


# ── ST3：每个 delta 只 tokenize 一次，不重算全文 ──────────────────

@pytest.mark.asyncio
async def test_st3_each_delta_tokenized_once_and_text_never_reaccumulated(monkeypatch):
    """O(n) 判据：tokenize 次数 == delta 数，且送入的文本总长 == 各 delta 之和。

    若改成「每次都重算全文」，总长会变成 n(n+1)/2 量级，
    这两条断言必定变红（不依赖任何计时）。
    """
    pieces: list[str] = []

    def _fake_estimate(model, text):
        pieces.append(text)
        return 1

    monkeypatch.setattr("service.llmService.core.tokenUtil.estimate_text_tokens", _fake_estimate)

    n = 120
    delta_len = 2
    recorder = await _run_stream(monkeypatch, [_chunk(content="ab") for _ in range(n)])

    assert len(pieces) == n, f"tokenize 次数 {len(pieces)} != delta 数 {n}"
    assert sum(len(p) for p in pieces) == n * delta_len, "送入的文本被重复累加（疑似重算全文）"
    assert max(len(p) for p in pieces) == delta_len
    assert recorder.items[-1].estimated_completion_tokens == n
    assert recorder.items[-1].completion_tokens is None


# ── ST4：网关给了 usage 就不本地累加 ────────────────────────────

@pytest.mark.asyncio
async def test_st4_usage_branch_skips_local_estimation(monkeypatch):
    calls: list[str] = []

    def _fake_estimate(model, text):
        calls.append(text)
        return 999  # 若被调用，估算值会明显错误

    monkeypatch.setattr("service.llmService.core.tokenUtil.estimate_text_tokens", _fake_estimate)

    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=123, total_tokens=133)
    recorder = await _run_stream(monkeypatch, [_chunk(content="hello", usage=usage)])

    assert calls == []
    # 带 usage 走真值键：`completion_tokens` / `total_tokens`，
    # 估算键必须**没有值**——否则前端会把真值显示成估算色（#169 M5）。
    assert recorder.items[-1].completion_tokens == 123
    assert recorder.items[-1].total_tokens == 133
    assert recorder.items[-1].estimated_completion_tokens is None


# ── ST5：tokenizer 抛异常时回退字符估算，流不中断 ────────────────

@pytest.mark.asyncio
async def test_st5_token_counter_failure_falls_back_without_breaking_stream(monkeypatch, caplog):
    def _boom(*args, **kwargs):
        raise RuntimeError("tokenizer exploded")

    monkeypatch.setattr(llmService.core.tokenUtil.litellm, "token_counter", _boom)

    with caplog.at_level(logging.WARNING, logger="util.tokenUtil"):
        recorder = await _run_stream(monkeypatch, [
            _chunk(content="abcdefgh"),  # 8 -> 2
            _chunk(content="ab"),        # 2 -> 1（max(1, 0) 兜底）
        ])

    # 回退口径：max(1, len(before) // 4)
    assert recorder.items[0].estimated_completion_tokens == 2
    assert recorder.items[-1].estimated_completion_tokens == 3
    # 回退路径同属估算分支，真值键同样必须缺席。
    assert [r.completion_tokens for r in recorder.items] == [None, None]
    noisy = [r for r in caplog.records if r.name == "util.tokenUtil" and r.levelno >= logging.WARNING]
    assert noisy == [], f"回退路径不应刷 warning：{[r.getMessage() for r in noisy]}"


# ── ST7：节流生效 ────────────────────────────────────────────

class _ThrottleHarness:
    """直接驱动 `_make_progress_callback`，记录每次 update 调用。"""

    def __init__(self):
        self.calls: list = []

    def install(self, monkeypatch, monotonic_fn):
        update = AsyncMock(return_value=SimpleNamespace(id=1))
        self.calls = update

        async def _record(activity_id, *, metadata_patch=None, persist=True, **kwargs):
            return await update(activity_id, metadata_patch=metadata_patch, persist=persist, **kwargs)

        monkeypatch.setattr(
            "service.agentService.agentTurnRunner.agentActivityService.update_activity_progress",
            _record,
        )
        # 只替换 runner 模块看到的 time，避免动全局 time.monotonic（asyncio 事件循环也依赖它）
        monkeypatch.setattr(
            "service.agentService.agentTurnRunner.time",
            SimpleNamespace(monotonic=monotonic_fn),
        )

    def callback(self, activity_id: int = 1):
        # _make_progress_callback 只依赖模块级常量与模块级 agentActivityService，不使用 self
        return AgentTurnRunner._make_progress_callback(None, activity_id)


def _progress(value: int):
    return llmService.InferStreamProgress(delta_text="x", estimated_completion_tokens=value)


@pytest.mark.asyncio
async def test_st7_chunk_count_throttle_limits_updates(monkeypatch):
    """100 个 delta 且时间不走动 → 只有每 10 个 chunk 才更新一次。"""
    harness = _ThrottleHarness()
    harness.install(monkeypatch, lambda: 1000.0)
    on_progress = harness.callback()

    for i in range(100):
        await on_progress(_progress(i + 1))

    assert harness.calls.await_count == 100 // _PROGRESS_THROTTLE_CHUNK_COUNT
    assert all(c.kwargs["persist"] is False for c in harness.calls.await_args_list)
    pushed = [c.kwargs["metadata_patch"].estimated_completion_tokens for c in harness.calls.await_args_list]
    assert pushed == [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]


@pytest.mark.asyncio
async def test_st7_time_throttle_alone_triggers_update(monkeypatch):
    """时间线独立生效：delta 间隔 0.5s > 0.2s 阈值 → 每个 delta 都触发更新。

    这里刻意用远离阈值的步长（0.5 vs 0.2），避免浮点累加在等号附近抖动；
    与 chunk 计数用例配合，证明「时间 OR chunk」两条线各自都能单独放行。
    """
    ticks = {"t": 0.0}

    def _monotonic():
        ticks["t"] += 0.5
        return ticks["t"]

    harness = _ThrottleHarness()
    harness.install(monkeypatch, _monotonic)
    on_progress = harness.callback()

    delta_count = 5
    for i in range(delta_count):
        await on_progress(_progress(i + 1))

    assert harness.calls.await_count == delta_count, "时间线没有按阈值放行"
    assert all(c.kwargs["persist"] is False for c in harness.calls.await_args_list)


@pytest.mark.asyncio
async def test_st7_fast_burst_updates_far_less_than_delta_count(monkeypatch):
    """高频突发（500 delta，每个 20ms）→ 更新次数远小于 delta 数。

    20ms × 10 = 200ms，时间线与 chunk 线同源，因此每 10 个 delta 一次，
    理想值 50。浮点在等号附近可能有 ±2 抖动，所以断言区间而非等值。
    """
    ticks = {"t": 0.0}

    def _monotonic():
        ticks["t"] += 0.02
        return ticks["t"]

    harness = _ThrottleHarness()
    harness.install(monkeypatch, _monotonic)
    on_progress = harness.callback()

    delta_count = 500
    for i in range(delta_count):
        await on_progress(_progress(i + 1))

    updates = harness.calls.await_count
    ideal = delta_count // _PROGRESS_THROTTLE_CHUNK_COUNT
    assert ideal - 2 <= updates <= ideal + 2, f"更新次数 {updates} 偏离理想值 {ideal}"
    assert updates * 5 <= delta_count, f"更新 {updates} 次，未达到「远小于 {delta_count}」"
    assert all(c.kwargs["persist"] is False for c in harness.calls.await_args_list)
