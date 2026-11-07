"""手动 compact 与 turn / 另一次 compact 的互斥（#141 返工 ①）。

**入口必须是真实路径**：`AgentCompactHandler.post` → `Agent.compact_context()` →
`AgentTurnRunner.execute_manual_compact()`。不走 `runner.execute_manual_compact`，
因为那会绕过最外层的 `try_acquire_manual_compact()` 占用——测的就不是交付物了。

判据（PM 04:58 裁定，替换 04:26 的「mem/db 同 id 正文一致」主判据）：

1. `_items` 里 `COMPACT_SUMMARY` 条数 **== 1**；
2. `build_infer_messages()` 里摘要条数 **≤ 1**（两条摘要同时进 LLM 请求 = token 与
   语义双污染，而「同 id 正文一致」在 A1 形态下是全绿的，所以不能当主判据）；
3. 三个挂点下第二次请求都必须是 400 `compact_in_progress`——A2/D 尤其不能是
   `no_compactable_history`（用户点两下看到「历史不足」，会以为数据坏了）：
   - **A1**：`insert_compact_summary` 之前（摘要还没落库）；
   - **A2**：`_refresh_system_prompt`（摘要已插入、正在收尾）；
   - **D**：`_finish_activity(SUCCEEDED)`——`_execute_compact` 的**最后一个 await**。
     A1/A2 的挂点都在它前面，只能覆盖「复位点在挂点之前」的实现；D 的挂点在最后一个
     await 的入口，才覆盖「复位点落在 `_refresh_system_prompt` 返回之后、
     `_finish_activity` 之前」这一类（PM 05:36 指出 A2 挂点覆盖不到，见 D 的 docstring）；
4. 第一次彻底结束后追加一轮对话，第二次必须 200（防「忘了复位」）；
5. compact 在途时新 job 不得把 turn 拉起来（`consume()` 的等待要真的起作用）。

挂点全部用 `asyncio.Event` 显式编排，不用 `sleep` 撞窗口。
"""

from __future__ import annotations

import asyncio
import unittest.mock as mock
from unittest.mock import AsyncMock

import pytest

import service.llmService as llmService
import service.ormService as ormService
from constants import (
    AgentActivityStatus,
    AgentHistoryStatus,
    AgentHistoryTag,
    DriverType,
    OpenaiApiRole,
    ScheduleJobType,
)
from controller import agentController
from dal.db import gtAgentHistoryManager, gtScheduleJobManager
from exception import TogoException
from model.dbModel.agentMessage import AgentMessage
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtAgentHistory import GtAgentHistory
from service.agentService.agent import Agent
from service.agentService.agentHistoryStore import AgentHistoryStore
from service.agentService.agentTurnRunner import AgentTurnRunner
from service.agentService.driver import AgentDriverConfig
from tests.base import ServiceTestCase
from tests.integration.test_compact_flow.test import (
    _ACTIVITY_PATCH,
    _CONFIG_PATCH,
    _INFER_PATCH,
    _make_mock_response,
    _mock_activity_service,
    _mock_config,
)

_AGENT_ID = 99
_SUMMARY_MARKER = "以下是之前对话的压缩摘要"


# ─── 挂点：显式事件编排 ────────────────────────────────────


class _Gate:
    """一个「走到这里就停住、等我放行」的 await 点。"""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()


def _gated_infer(gate: _Gate, summary: str):
    """A1 挂点：把摘要那次 LLM 调用卡住（此时还没走到 `insert_compact_summary`）。"""

    async def _infer(*args, **kwargs):
        gate.entered.set()
        await gate.release.wait()
        return llmService.InferResult.success(_make_mock_response(summary))

    return _infer


def _gated_refresh_fn(gate: _Gate):
    """A2 挂点：卡在 `_refresh_system_prompt`。

    `_execute_compact` 的 await 顺序是
    `compact_messages` → `insert_compact_summary` → `_refresh_system_prompt` → `_finish_activity`，
    所以停在这里 = 「摘要已插入、正在收尾」。

    覆盖范围仅限「复位点在 `_refresh_system_prompt` 入口之前」的实现——挂在中间只能
    看到挂点之前发生的事。复位点若落在 refresh 返回之后、`_finish_activity` 之前，
    本用例**看不到**（那时 gate 已放行，没人再查）。这个窗口由 D 用例守（挂点就在
    最后一个 await 的入口）。
    """

    async def _refresh() -> str:
        gate.entered.set()
        await gate.release.wait()
        return ""

    return _refresh


def _gated_finish(gate: _Gate, calls: dict):
    """D 挂点：卡在 `_finish_activity(SUCCEEDED)`——`_execute_compact` 的最后一个 await。

    只在 `status == SUCCEEDED` 那一次挂住（FAILED 分支也会走 `_finish_activity`，
    若不区分，`compact_messages` 失败路径会先触发挂点，用例就测不到正常收尾）。
    `calls["succeeded"]` 记录成功分支被调用次数：它必须恰为 1，否则「只挂一次」
    这个前提本身没被验证。
    """
    real = AgentTurnRunner._finish_activity  # ⚠ 必须在 patch 之前取原函数

    async def _wrapper(self_runner, activity_id, *, status, **kwargs):
        if status == AgentActivityStatus.SUCCEEDED:
            calls["succeeded"] = calls.get("succeeded", 0) + 1
            gate.entered.set()
            await gate.release.wait()
        return await real(self_runner, activity_id, status=status, **kwargs)

    return _wrapper


# ─── 构造真实对象 ──────────────────────────────────────────


async def _seed_history(agent_id: int, turns: int = 5) -> AgentHistoryStore:
    """清空该 agent 的历史并写入 N 轮对话 + 一条待回复的用户消息。"""
    await GtAgentHistory.delete().where(GtAgentHistory.agent_id == agent_id).aio_execute()  # type: ignore[attr-defined]
    history = AgentHistoryStore(agent_id=agent_id)
    for i in range(turns):
        await history.append_history_message(GtAgentHistory.build(
            AgentMessage(role=OpenaiApiRole.USER, content=f"用户消息 {i}"),
            status=AgentHistoryStatus.SUCCESS,
        ))
        await history.append_history_message(GtAgentHistory.build(
            AgentMessage(role=OpenaiApiRole.ASSISTANT, content=f"助手回复 {i}"),
            status=AgentHistoryStatus.SUCCESS,
        ))
    await history.append_history_message(GtAgentHistory.build(
        AgentMessage(role=OpenaiApiRole.USER, content="最新的用户输入"),
        status=AgentHistoryStatus.SUCCESS,
    ))
    return history


def _make_agent(history: AgentHistoryStore, *, prompt_refresh_fn=None) -> Agent:
    gt_agent = GtAgent(id=_AGENT_ID, team_id=1, name="CompactBot", role_template_id=1, model="mock-model@svc")
    agent = Agent(
        gt_agent=gt_agent,
        system_prompt="You are a helpful assistant.",
        driver_config=AgentDriverConfig(driver_type=DriverType.NATIVE),
        prompt_refresh_fn=prompt_refresh_fn,
    )
    agent.job_consumer._turn_runner._history = history
    return agent


def _build_handler(agent_id: int = _AGENT_ID):
    handler = object.__new__(agentController.AgentCompactHandler)
    handler.agent_id_str = str(agent_id)
    captured: list = []
    handler.return_json = captured.append
    return handler, captured


def _patch_runtime(monkeypatch, agent) -> None:
    monkeypatch.setattr(agentController.agentService, "get_agent_or_none", lambda _id: agent)


# ─── 判据辅助 ──────────────────────────────────────────────


def _memory_summary_count(history: AgentHistoryStore) -> int:
    return sum(1 for item in history if AgentHistoryTag.COMPACT_SUMMARY in item.tags)


def _infer_summary_count(history: AgentHistoryStore) -> int:
    return sum(1 for msg in history.build_infer_messages() if _SUMMARY_MARKER in (msg.content or ""))


async def _db_summary_rows(agent_id: int) -> list[GtAgentHistory]:
    rows = await gtAgentHistoryManager.get_agent_history(agent_id)
    return [row for row in rows if AgentHistoryTag.COMPACT_SUMMARY in row.tags]


async def _append_one_round(history: AgentHistoryStore) -> None:
    """在 history 末尾追加「助手回复 + 新用户输入」一轮，让下一次 compact 有得压。"""
    await history.append_history_message(GtAgentHistory.build(
        AgentMessage(role=OpenaiApiRole.ASSISTANT, content="助手回复：上一轮结论"),
        status=AgentHistoryStatus.SUCCESS,
    ))
    await history.append_history_message(GtAgentHistory.build(
        AgentMessage(role=OpenaiApiRole.USER, content="再问一个问题的用户输入"),
        status=AgentHistoryStatus.SUCCESS,
    ))


class TestManualCompactMutex(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        await ormService.startup(cls._get_test_db_path())

    @classmethod
    async def async_teardown_class(cls):
        await ormService.shutdown()

    # ── 判据 3 + 1 + 2：A1 挂点 ──────────────────────────────

    async def test_second_request_during_llm_call_gets_compact_in_progress(self, monkeypatch):
        """A1：第一次还没走到 `insert_compact_summary`，第二次必须 400 而不是 200。

        病灶形态（未修）：两次请求各自算出一个 `insert_seq`，都返回 200；若撞到同一
        seq，库按 `(agent_id, seq)` 回读把后一次正文换成前一次；若不撞号，两条摘要
        都进 `build_infer_messages()` → LLM 同时看到两份摘要。
        所以主判据不是「同 id 正文一致」，而是「摘要条数」。
        """
        history = await _seed_history(_AGENT_ID)
        agent = _make_agent(history)
        _patch_runtime(monkeypatch, agent)

        gate = _Gate()
        handler, captured = _build_handler()

        with (
            mock.patch(_CONFIG_PATCH, return_value=_mock_config()),
            mock.patch(_INFER_PATCH, _gated_infer(gate, "这是压缩摘要：之前讨论了5轮对话。")),
            mock.patch(_ACTIVITY_PATCH, _mock_activity_service()),
        ):
            first = asyncio.create_task(handler.post(str(_AGENT_ID)))
            await asyncio.wait_for(gate.entered.wait(), 5)

            # 业务断言放在最前面：红的时候直接告诉你「第二次拿到了什么错误码」。
            # 若把 `is_compacting is True` 放在前面，实现过早复位时这里先红，
            # 就看不到真正的病灶（A2 下第二次会拿到 `no_compactable_history`）。
            with pytest.raises(TogoException) as exc_info:
                await handler.post(str(_AGENT_ID))
            assert exc_info.value.error_code == "compact_in_progress"

            # 诊断用：挂点必须真的挂住（第一次此刻卡在 LLM 调用里）
            assert agent.is_compacting is True

            gate.release.set()
            await asyncio.wait_for(first, 10)

        assert captured[0]["success"] is True

        # 判据 1 / 2：内存与库里都只允许有一条摘要
        assert _memory_summary_count(history) == 1, "内存里出现了两条 COMPACT_SUMMARY"
        assert _infer_summary_count(history) <= 1, "两条摘要都进了 LLM 请求"
        assert len(await _db_summary_rows(_AGENT_ID)) == 1, "库里出现了两条 COMPACT_SUMMARY"

        # 判据 4 的前置：临界区必须已复位
        assert agent.is_compacting is False

    # ── 判据 3 + 1 + 2：A2 挂点 ──────────────────────────────

    async def test_second_request_during_finishing_gets_compact_in_progress_not_no_history(
        self, monkeypatch
    ):
        """A2：第一次已插入摘要、正在收尾，第二次必须 400 `compact_in_progress`。

        未修时的实际行为是 `no_compactable_history`——第二次 `reload_from_db()` 看到
        「摘要 + 最新用户消息」，`_skip_trailing_users` 一路跳到 `preserve_start_idx=0`
        ⇒ `build_compact_plan()` 返回 None。用户点两下看到「历史不足」，会以为数据坏了。
        """
        history = await _seed_history(_AGENT_ID)
        gate = _Gate()
        agent = _make_agent(history, prompt_refresh_fn=_gated_refresh_fn(gate))
        _patch_runtime(monkeypatch, agent)

        handler, captured = _build_handler()

        with (
            mock.patch(_CONFIG_PATCH, return_value=_mock_config()),
            mock.patch(_INFER_PATCH, AsyncMock(
                return_value=llmService.InferResult.success(_make_mock_response("这是压缩摘要：A2 挂点。")),
            )),
            mock.patch(_ACTIVITY_PATCH, _mock_activity_service()),
        ):
            first = asyncio.create_task(handler.post(str(_AGENT_ID)))
            await asyncio.wait_for(gate.entered.wait(), 5)

            # 业务断言放在最前面（理由同 A1 用例）：过早复位的实现会在这里报出
            # 实际拿到的错误码，而不是先在前置 flag 上红。
            with pytest.raises(TogoException) as exc_info:
                await handler.post(str(_AGENT_ID))
            assert exc_info.value.error_code == "compact_in_progress"

            # 此刻摘要已经插进内存与库
            assert _memory_summary_count(history) == 1
            assert agent.is_compacting is True

            gate.release.set()
            await asyncio.wait_for(first, 10)

        assert captured[0]["success"] is True
        assert _memory_summary_count(history) == 1
        assert _infer_summary_count(history) <= 1
        assert len(await _db_summary_rows(_AGENT_ID)) == 1
        assert agent.is_compacting is False

    # ── 判据 3：D 挂点 = 最后一个 await ────────────────────────

    async def test_second_request_at_last_await_gets_compact_in_progress(self, monkeypatch):
        """D：第一次卡在 `_finish_activity(SUCCEEDED)`（`_execute_compact` 的最后一个
        await 的入口），第二次必须 400 `compact_in_progress`。

        与 A2 的区别（PM 05:36 指出）：A2 的 gate 停在 `_refresh_system_prompt` 内部，
        那时 `_compacting` 还是 True，所以**「在 refresh 返回之后、`_finish_activity`
        之前释放 `_compacting`」这个复位点 A2 完全看不到**。D 把 gate 放在最后一个 await
        的入口，复位点无论落在 refresh 之后、还是 `_finish_activity` 调用之前，都会让
        第二次请求挤进来 ⇒ 本用例转红。

        只覆盖「复位点在 `_finish_activity` 入口之前」——`_finish_activity` 自身内部
        释放属于更晚的窗口，但那已经不是 `_execute_compact` 的范围了（见 `calls`
        计数断言：成功分支只应走一次）。
        """
        history = await _seed_history(_AGENT_ID)
        agent = _make_agent(history)
        _patch_runtime(monkeypatch, agent)

        gate = _Gate()
        calls: dict = {}
        handler, captured = _build_handler()

        with (
            mock.patch(_CONFIG_PATCH, return_value=_mock_config()),
            mock.patch(_INFER_PATCH, AsyncMock(
                return_value=llmService.InferResult.success(_make_mock_response("这是压缩摘要：D 挂点。")),
            )),
            mock.patch(_ACTIVITY_PATCH, _mock_activity_service()),
            mock.patch.object(AgentTurnRunner, "_finish_activity", _gated_finish(gate, calls)),
        ):
            first = asyncio.create_task(handler.post(str(_AGENT_ID)))
            await asyncio.wait_for(gate.entered.wait(), 5)

            # 业务断言放最前面（同 A1/A2）：复位点提前时这里直接红在「第二次拿到了
            # 什么」，而不是先在前置 flag 上红，掩盖真正的病灶。
            with pytest.raises(TogoException) as exc_info:
                await handler.post(str(_AGENT_ID))
            assert exc_info.value.error_code == "compact_in_progress"

            # 前提自证：此刻已走到最后一个 await，摘要与内存裁剪都已完成。
            assert _memory_summary_count(history) == 1
            assert agent.is_compacting is True

            gate.release.set()
            await asyncio.wait_for(first, 10)

        assert calls.get("succeeded") == 1, "成功分支的 _finish_activity 应只被调用一次"
        assert captured[0]["success"] is True
        assert _memory_summary_count(history) == 1
        assert _infer_summary_count(history) <= 1
        assert len(await _db_summary_rows(_AGENT_ID)) == 1
        assert agent.is_compacting is False

    # ── 判据 4：防忘复位 ────────────────────────────────────

    async def test_second_request_after_first_fully_finished_succeeds(self, monkeypatch):
        """第一次彻底结束后再点一次必须 200。

        没有这条，「把 `_compacting` 置 True 后永不复位」也能通过上面两条用例。
        """
        history = await _seed_history(_AGENT_ID)
        agent = _make_agent(history)
        _patch_runtime(monkeypatch, agent)

        handler, captured = _build_handler()

        with (
            mock.patch(_CONFIG_PATCH, return_value=_mock_config()),
            mock.patch(_INFER_PATCH, AsyncMock(
                return_value=llmService.InferResult.success(_make_mock_response("第一次摘要")),
            )),
            mock.patch(_ACTIVITY_PATCH, _mock_activity_service()),
        ):
            await handler.post(str(_AGENT_ID))
            assert agent.is_compacting is False, "第一次结束后临界区没复位"

            await _append_one_round(history)

            with mock.patch(_INFER_PATCH, AsyncMock(
                return_value=llmService.InferResult.success(_make_mock_response("第二次摘要")),
            )):
                await handler.post(str(_AGENT_ID))

        assert len(captured) == 2
        assert all(body["success"] is True for body in captured)
        # 第二次把旧摘要一起压掉，内存里仍只有一条
        assert _memory_summary_count(history) == 1
        assert _infer_summary_count(history) <= 1

    # ── 判据 5：turn vs compact ─────────────────────────────

    async def test_new_job_does_not_start_turn_while_compacting(self, monkeypatch):
        """compact 在途时新 job 不得把 turn 拉起来。

        未修时 `consume()` 无脑 `_set_status(ACTIVE)` 后取 job 开跑，turn 与 compact
        抢同一份 `_history`：compact 的 `reload_from_db()` 会整体覆盖 `_items`，
        把 turn 刚 append 的消息抹掉。

        判定用「`get_first_unfinished_job` 是否被调用」而不是 sleep 一段时间看结果：
        取 job 是 `consume()` 通过等待后的第一个动作，它被调用就说明等待没起作用。
        """
        history = await _seed_history(_AGENT_ID)
        agent = _make_agent(history)
        consumer = agent.job_consumer

        runner = consumer._turn_runner
        runner.run_task_turn = AsyncMock()  # type: ignore[method-assign]

        job = await gtScheduleJobManager.create_job(_AGENT_ID, ScheduleJobType.ROOM_MESSAGE, {})

        gate = _Gate()
        handler, captured = _build_handler()
        _patch_runtime(monkeypatch, agent)

        job_fetched = asyncio.Event()
        real_get_first = gtScheduleJobManager.get_first_unfinished_job

        async def _spy_get_first(agent_id: int):
            job_fetched.set()
            return await real_get_first(agent_id)

        with (
            mock.patch(_CONFIG_PATCH, return_value=_mock_config()),
            mock.patch(_INFER_PATCH, _gated_infer(gate, "这是压缩摘要：turn 互斥。")),
            mock.patch(_ACTIVITY_PATCH, _mock_activity_service()),
            mock.patch(
                "service.agentService.jobConsumer.gtScheduleJobManager.get_first_unfinished_job",
                _spy_get_first,
            ),
        ):
            compact_task = asyncio.create_task(handler.post(str(_AGENT_ID)))
            await asyncio.wait_for(gate.entered.wait(), 5)
            assert agent.is_compacting is True

            consume_task = asyncio.create_task(consumer.consume())

            # compact 仍卡着：consume 必须停在等待里，连 job 都不该去取
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(job_fetched.wait(), 0.25)
            runner.run_task_turn.assert_not_awaited()

            gate.release.set()
            await asyncio.wait_for(compact_task, 10)
            await asyncio.wait_for(job_fetched.wait(), 5)
            await asyncio.wait_for(consume_task, 10)

        assert captured[0]["success"] is True
        runner.run_task_turn.assert_awaited_once_with(job)
        assert _memory_summary_count(history) == 1
        assert _infer_summary_count(history) <= 1
