"""`AgentCompactHandler`（`POST /agents/<id>/compact.json`）的错误映射回归。

本文件守的核心不变量：**只有「无可压缩历史」这一种业务拒绝转 400，
其余异常必须原样冒出去变成 500**。写错这一条，真实故障（LLM 挂了、
摘要返回空）会被伪装成「当前无足够可压缩的历史消息」的业务提示，
用户点一次没反应、日志里也看不到故障——这是最坏的一种「兜底」。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from constants import AgentStatus
from controller import agentController
from exception import TogoException
from model.coreModel.agentModel import AgentContextUsage
from service.agentService.compact import NoCompactableHistoryError


def _usage() -> AgentContextUsage:
    return AgentContextUsage(
        used_tokens=1000,
        limit_tokens=114688,
        compact_trigger_tokens=97484,
        context_window_tokens=131072,
        ratio=1000 / 114688,
    )


def _agent(
    *,
    status: AgentStatus,
    compact_result=None,
    compact_error: Exception | None = None,
    is_compacting: bool = False,
):
    """构造一个「只实现本 handler 会用到的那几个成员」的 Agent 替身。

    `is_compacting` 必须显式给出：handler 在调 `compact_context()` 之前会读它
    做「已有压缩在跑」的预检。替身漏掉这个属性会直接 AttributeError，
    而 AttributeError 冒出去是 500——测试会以「莫名 500」的形态红掉，
    看不出真正原因是替身不完整。
    """

    async def _compact_context():
        if compact_error is not None:
            raise compact_error
        return compact_result

    return SimpleNamespace(
        status=status,
        is_compacting=is_compacting,
        compact_context=_compact_context,
        gt_agent=SimpleNamespace(id=5, team_id=2),
    )


def _build_handler(agent_id: str = "5"):
    handler = object.__new__(agentController.AgentCompactHandler)
    handler.agent_id_str = agent_id
    captured: dict = {"json": []}
    handler.return_json = captured["json"].append
    return handler, captured


def _patch_runtime(monkeypatch, agent) -> None:
    monkeypatch.setattr(agentController.agentService, "get_agent_or_none", lambda _id: agent)


# ─── 成功路径 ────────────────────────────────────────────


async def test_compact_returns_latest_usage_on_success(monkeypatch):
    """成功后回最新用量，且字段与 `AgentContextUsage` 一致（前端直接吃）。"""
    agent = _agent(status=AgentStatus.IDLE, compact_result=_usage())
    _patch_runtime(monkeypatch, agent)

    handler, captured = _build_handler()
    await handler.post("5")

    assert len(captured["json"]) == 1
    body = captured["json"][0]
    assert body["success"] is True
    assert body["agent_id"] == 5
    usage = body["context_usage"]
    assert usage["used_tokens"] == 1000
    assert usage["limit_tokens"] == 114688
    assert usage["compact_trigger_tokens"] == 97484
    assert usage["context_window_tokens"] == 131072


# ─── 业务拒绝：必须 400 且带专属错误码 ───────────────────


async def test_compact_maps_no_compactable_history_to_400(monkeypatch):
    """无可压缩历史 → 400 `no_compactable_history`，**不是 500**。

    这是本文件最重要的断言。`NoCompactableHistoryError` 继承 `RuntimeError`，
    所以如果 handler 里写成 `except RuntimeError`，这里仍会「看起来」变成 400，
    因此下面还有一条用例专门证明**其它** RuntimeError 不会被误吞。
    """
    agent = _agent(
        status=AgentStatus.IDLE,
        compact_error=NoCompactableHistoryError("compact 跳过：无可压缩消息"),
    )
    _patch_runtime(monkeypatch, agent)

    handler, captured = _build_handler()
    with pytest.raises(TogoException) as exc_info:
        await handler.post("5")

    assert exc_info.value.error_code == "no_compactable_history"
    assert captured["json"] == []  # 没有回 success 响应


async def test_compact_does_not_swallow_other_runtime_errors(monkeypatch):
    """LLM 失败（普通 RuntimeError）必须原样冒出去 → 由 `write_error` 变 500。

    如果实现里把 `except NoCompactableHistoryError` 写成 `except RuntimeError`，
    这条用例会红——那正是「兜底越完善、缺陷越隐蔽」的具体形态。
    """
    agent = _agent(
        status=AgentStatus.IDLE,
        compact_error=RuntimeError("LLM inference failed during compact"),
    )
    _patch_runtime(monkeypatch, agent)

    handler, captured = _build_handler()
    with pytest.raises(RuntimeError) as exc_info:
        await handler.post("5")

    # 关键：抛的是原始 RuntimeError，**不是**被翻译成业务拒绝
    assert not isinstance(exc_info.value, NoCompactableHistoryError)
    assert "LLM inference failed" in str(exc_info.value)
    assert captured["json"] == []


# ─── ACTIVE 拦截 ─────────────────────────────────────────


async def test_compact_rejects_when_agent_is_active(monkeypatch):
    """ACTIVE 期间拒绝：活跃 turn 正在写同一份 _history，压缩会与之打架。"""
    called = {"count": 0}

    async def _should_not_run():
        called["count"] += 1
        return _usage()

    agent = SimpleNamespace(
        status=AgentStatus.ACTIVE,
        is_compacting=False,
        compact_context=_should_not_run,
        gt_agent=SimpleNamespace(id=5, team_id=2),
    )
    _patch_runtime(monkeypatch, agent)

    handler, captured = _build_handler()
    with pytest.raises(TogoException) as exc_info:
        await handler.post("5")

    assert exc_info.value.error_code == "agent_is_active"
    # 拦截必须发生在真正压缩之前
    assert called["count"] == 0
    assert captured["json"] == []


# ─── 并发互斥：已有压缩在跑 ───────────────────────────────


async def test_compact_rejects_when_compaction_in_progress(monkeypatch):
    """已有一次手动压缩在跑 → 400 `compact_in_progress`，且**不排队、不执行**。

    这是「用户连点两次压缩」的入口形态。如果这里改成排队（等前一次结束再跑），
    用户仍然会得到两次压缩，只是变成串行的——防并发变成串行执行，
    两次压缩各自重算 insert_seq，正是 `agentHistoryStore` 同 id 覆盖的病灶入口。
    所以断言的核心是 `called["count"] == 0`：第二次请求根本不进入压缩。
    """
    called = {"count": 0}

    async def _should_not_run():
        called["count"] += 1
        return _usage()

    agent = SimpleNamespace(
        status=AgentStatus.IDLE,
        is_compacting=True,
        compact_context=_should_not_run,
        gt_agent=SimpleNamespace(id=5, team_id=2),
    )
    _patch_runtime(monkeypatch, agent)

    handler, captured = _build_handler()
    with pytest.raises(TogoException) as exc_info:
        await handler.post("5")

    assert exc_info.value.error_code == "compact_in_progress"
    assert called["count"] == 0
    assert captured["json"] == []


async def test_compact_maps_compact_in_progress_raised_inside_to_400(monkeypatch):
    """预检通过、但 `compact_context()` 内部占用失败 → 仍是 400 `compact_in_progress`。

    这条覆盖「检查 → 占用」之间被将来的 await 打开的窄窗口：
    handler 先读 `is_compacting`（False），随后 `compact_context()` 进门同步占用
    时发现已被别人占了，抛 `CompactInProgressError`。若不在这里翻译，
    它会作为 RuntimeError 冒出去变成 500，前端拿到的就是「服务故障」而非「稍候」。
    """
    from service.agentService.compact import CompactInProgressError

    agent = _agent(
        status=AgentStatus.IDLE,
        is_compacting=False,
        compact_error=CompactInProgressError("已有一次手动压缩正在进行"),
    )
    _patch_runtime(monkeypatch, agent)

    handler, captured = _build_handler()
    with pytest.raises(TogoException) as exc_info:
        await handler.post("5")

    assert exc_info.value.error_code == "compact_in_progress"
    assert captured["json"] == []


# ─── 未装载 / 不存在 ─────────────────────────────────────


async def test_compact_rejects_when_agent_not_loaded(monkeypatch):
    """未装载进内存 → 400 `agent_not_loaded`（不做离线装配，见 core 注释）。"""
    _patch_runtime(monkeypatch, None)

    handler, captured = _build_handler()
    with pytest.raises(TogoException) as exc_info:
        await handler.post("5")

    assert exc_info.value.error_code == "agent_not_loaded"
    assert captured["json"] == []
