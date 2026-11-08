"""Agent 长期记忆系统集成测试（#134 段3 / 段4）。

覆盖 step2 §7.2 的第 2 组要求（路径按仓库约定从平铺改为目录形态，PM 裁定 B）：

1. 同 Session 内写入星标记忆后 `Runner.system_prompt` **保持冻结**；
2. 触发 `_execute_compact()` 后 `Runner.system_prompt` **自动刷新**为最新星标快照（含 `[#id]`）；
3. `prompt_refresh_fn` 不传时行为与改前完全一致（**不刷新**）；
4. `GET /agents/<id>/memories.json` 接口返回，以及 `clear_agent_data` / `clear_team_data` 清理联动。

前三条用真实 SQLite（跑过 0020 迁移）+ mock LLM 走完整 compact 流程；第 4 条直接调
service / handler，不启后端子进程（保持与其他 controller 单测一致的轻量口径）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import db as db_tool
import service.llmService as llmService
import service.ormService as ormService
from constants import (
    AgentHistoryStatus,
    AgentHistoryTag,
    DriverType,
    MemoryStarLevel,
    MemoryStatus,
    OpenaiApiRole,
)
from controller import agentController
from exception import TogoException
from model.coreModel.memoryModel import memory_api_dict
from model.dbModel.agentMessage import AgentMessage
from model.dbModel.base import bind_database, unbind_database
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtAgentHistory import GtAgentHistory
from model.dbModel.gtAgentMemory import GtAgentMemory
from service import memoryService, teamService
from service.agentService.agentHistoryStore import AgentHistoryStore
from service.agentService.agentTurnRunner import AgentTurnRunner
from service.agentService.driver import AgentDriverConfig
from service.ormService import AioSqliteDatabase
from util import llmApiUtil

_MIGRATION_SQL = (
    Path(__file__).resolve().parents[3] / "assets" / "migrate" / "0020_add_agent_memories.sql"
)

_TEAM_ID = 2
_AGENT_ID = 99

_CONFIG_PATCH = "util.configUtil.get_app_config"
_INFER_PATCH = "service.agentService.compact.llmService.infer_stream"
_ESTIMATE_PATCH = "service.agentService.agentTurnRunner.compact.estimate_tokens"
_ACTIVITY_PATCH = "service.agentService.agentTurnRunner.agentActivityService"


# ---------------------------------------------------------------------------
# 夹具：真实 SQLite（只建记忆表 + 历史表）
# ---------------------------------------------------------------------------


@pytest.fixture
async def memory_db(tmp_path):
    """全量迁移出来的临时库（compact 流程要用历史表，记忆表来自 0020）。

    用 `db.migrate_database()` 跑**全部**迁移而不是只 `executescript` 0020：
    compact 流程会读写 `agent_history`，历史表由 0001 起的一系列迁移建立，
    只建记忆表会让用例在「表不存在」上失败，看不出真正的行为。
    """
    assert _MIGRATION_SQL.exists(), f"迁移文件不存在：{_MIGRATION_SQL}"

    db_path = tmp_path / "memory_flow.db"
    db_tool.migrate_database(
        str(db_path),
        migrations_dir=str(_MIGRATION_SQL.parent),
        workspace_root=str(tmp_path),
    )

    db = AioSqliteDatabase(str(db_path), pool_params={"timeout": 30})
    bind_database(db)
    await db.aio_connect()
    try:
        yield db
    finally:
        await db.aio_close()
        unbind_database()


def _mock_activity_service():
    activity = MagicMock()
    activity.id = 1
    module = MagicMock()
    module.add_activity = AsyncMock(return_value=activity)
    module.update_activity_progress = AsyncMock(return_value=activity)
    return module


def _mock_config():
    from util.configUtil.configTypes import (
        LlmContextConfig,
        LlmModelConfig,
        LlmProviderConfig,
        SettingConfig,
    )

    app_config = MagicMock()
    app_config.setting = SettingConfig(
        version="v2",
        language="zh-CN",
        workspace_root="/tmp/test_workspace",
        default_models={"primary": "mock-model@svc", "lite": "mock-model@svc", "vision": "mock-model@svc"},
        llm_providers=[
            LlmProviderConfig(
                name="svc",
                enable=True,
                type="openai",
                api_key="test-key",
                urls={"openai": "http://127.0.0.1:9999/v1/chat/completions"},
                models=[
                    LlmModelConfig(
                        name="mock-model",
                        protocol="openai",
                        context_config=LlmContextConfig(
                            compact_trigger_ratio=0.85,
                            reserve_output_tokens=0,
                            context_window_tokens=400,
                        ),
                    )
                ],
            )
        ],
    )
    return app_config


def _make_mock_response(content: str):
    message = llmApiUtil.OpenAIMessage(role=OpenaiApiRole.ASSISTANT, content=content)
    response = MagicMock()
    choice = MagicMock()
    choice.message = message
    response.choices = [choice]
    response.usage = None
    return response


def _make_runner(system_prompt: str, prompt_refresh_fn=None) -> AgentTurnRunner:
    gt_agent = GtAgent(id=_AGENT_ID, team_id=_TEAM_ID, name="MemoryBot", role_template_id=1, model="mock-model@svc")
    return AgentTurnRunner(
        gt_agent=gt_agent,
        system_prompt=system_prompt,
        driver_config=AgentDriverConfig(driver_type=DriverType.NATIVE),
        prompt_refresh_fn=prompt_refresh_fn,
    )


async def _build_history(turns: int = 3, agent_id: int = _AGENT_ID) -> AgentHistoryStore:
    """构建含 N 轮对话 + 1 条新用户消息的 history（compact 前置条件）。"""
    history = AgentHistoryStore(agent_id=agent_id)
    for index in range(turns):
        await history.append_history_message(GtAgentHistory.build(
            AgentMessage(role=OpenaiApiRole.USER, content=f"用户消息 {index}"),
            status=AgentHistoryStatus.SUCCESS,
        ))
        await history.append_history_message(GtAgentHistory.build(
            AgentMessage(role=OpenaiApiRole.ASSISTANT, content=f"助手回复 {index}"),
            status=AgentHistoryStatus.SUCCESS,
        ))
    await history.append_history_message(GtAgentHistory.build(
        AgentMessage(role=OpenaiApiRole.USER, content="最新的用户输入"),
        status=AgentHistoryStatus.SUCCESS,
    ))
    return history


async def _create_memory(title: str, **kwargs) -> None:
    params = {
        "team_id": _TEAM_ID,
        "agent_id": _AGENT_ID,
        "title": title,
        "summary": f"{title} 的概要",
        "content": f"{title} 的正文",
    }
    params.update(kwargs)
    await memoryService.create_memory(**params)


def _render_snapshot_prompt(starred: list[GtAgentMemory]) -> str:
    """模拟 promptBuilder 渲染出的 System Prompt（只保留快照区块，便于断言）。"""
    from service.agentService.promptRenderer import render_prompt

    return render_prompt("system_prompt.j2", starred_memories=starred)


# ---------------------------------------------------------------------------
# ① 同 Session 内：写入星标记忆后 system_prompt 冻结
# ---------------------------------------------------------------------------


async def test_system_prompt_frozen_within_session(memory_db):
    """会话中途 `memory_create` / `memory_update` 落库，但**不**改当前 system_prompt。

    Prompt 冻结是 Prompt Cache 命中的前提（step2 §1.2 原则 3）：会话中途换
    System Prompt 会让模型侧缓存整体失效，所以写入只落库、不动 prompt。
    """
    await _create_memory("初始偏好", star_level="summary")
    starred = await memoryService.get_starred_memories(_TEAM_ID, _AGENT_ID)
    initial_prompt = _render_snapshot_prompt(starred)
    assert "[#1][SUMMARY] 初始偏好" in initial_prompt

    refresh_calls: list[int] = []

    async def _refresh() -> str:
        refresh_calls.append(1)
        latest = await memoryService.get_starred_memories(_TEAM_ID, _AGENT_ID)
        return _render_snapshot_prompt(latest)

    runner = _make_runner(initial_prompt, prompt_refresh_fn=_refresh)

    # 会话中途写入一条新的概要级星标
    await _create_memory("会话中途新增", star_level="summary")
    # 也做一次归档（会让快照内容变化）
    await memoryService.update_memory(_TEAM_ID, _AGENT_ID, 1, status="archived")

    # 数据确实变了（落库生效）
    latest = await memoryService.get_starred_memories(_TEAM_ID, _AGENT_ID)
    assert [memory.title for memory in latest] == ["会话中途新增"]

    # 但 runner 的 prompt 与 refresh 调用次数都没变
    assert runner.system_prompt == initial_prompt
    assert "[#1][SUMMARY] 初始偏好" in runner.system_prompt
    assert "会话中途新增" not in runner.system_prompt
    assert refresh_calls == []


# ---------------------------------------------------------------------------
# ② compact 后：system_prompt 自动刷新为最新快照
# ---------------------------------------------------------------------------


async def test_system_prompt_refreshed_after_compact(memory_db):
    """`_execute_compact()` 切新 session 后刷新 prompt，含最新 `[#id]` 与概要。"""
    await _create_memory("过时规则", star_level="summary")
    starred = await memoryService.get_starred_memories(_TEAM_ID, _AGENT_ID)
    initial_prompt = _render_snapshot_prompt(starred)
    assert "过时规则" in initial_prompt

    async def _refresh() -> str:
        latest = await memoryService.get_starred_memories(_TEAM_ID, _AGENT_ID)
        return _render_snapshot_prompt(latest)

    history = await _build_history()
    runner = _make_runner(initial_prompt, prompt_refresh_fn=_refresh)
    runner._history = history

    # 会话中途：旧规则归档 + 新增两条星标（1 条 summary、1 条 title）
    await memoryService.update_memory(_TEAM_ID, _AGENT_ID, 1, status="archived")
    await _create_memory("新方案输出规范", summary="先结论后细节", star_level="summary")
    await _create_memory("存储层选型约束", star_level="title")

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_PATCH, AsyncMock(
            return_value=llmService.InferResult.success(_make_mock_response("压缩摘要")),
        )),
        patch(_ACTIVITY_PATCH, _mock_activity_service()),
    ):
        await runner._execute_compact()

    prompt = runner.system_prompt
    assert "过时规则" not in prompt, "归档记忆不应出现在刷新后的快照里"
    assert "[SUMMARY]" in prompt and "新方案输出规范" in prompt
    assert "[TITLE]" in prompt and "存储层选型约束" in prompt
    # 星标清单带 `#id`，Agent 可直接按 ID 更新 / 归档
    assert "#2" in prompt and "#3" in prompt
    # summary 级带结论概要
    assert "先结论后细节" in prompt


async def test_refresh_happens_after_session_switch(memory_db):
    """刷新点必须在 `insert_compact_summary` 之后（新 session 已建立）。"""
    history = await _build_history()
    order: list[str] = []

    original_insert = AgentHistoryStore.insert_compact_summary

    async def _tracked_insert(self, *args, **kwargs):
        order.append("insert")
        return await original_insert(self, *args, **kwargs)

    async def _refresh() -> str:
        order.append("refresh")
        return "刷新后的 prompt"

    runner = _make_runner("初始 prompt", prompt_refresh_fn=_refresh)
    runner._history = history

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_PATCH, AsyncMock(
            return_value=llmService.InferResult.success(_make_mock_response("摘要")),
        )),
        patch(_ACTIVITY_PATCH, _mock_activity_service()),
        patch.object(AgentHistoryStore, "insert_compact_summary", _tracked_insert),
    ):
        await runner._execute_compact()

    assert order[:2] == ["insert", "refresh"]
    assert runner.system_prompt == "刷新后的 prompt"


# ---------------------------------------------------------------------------
# ③ 不传 prompt_refresh_fn：行为与改前完全一致
# ---------------------------------------------------------------------------


async def test_compact_without_refresh_fn_keeps_prompt_unchanged(memory_db):
    """默认 `None` ⇒ 不刷新，`system_prompt` 保持构造时的值（改前行为）。

    这条是「默认参数不改变既有行为」的证明：老代码构造 Runner 时不传该参数，
    compact 之后 prompt 必须仍是原来那份。
    """
    history = await _build_history()
    original_prompt = "构造时的 System Prompt（无快照）"
    runner = _make_runner(original_prompt)  # 不传 prompt_refresh_fn
    runner._history = history
    assert runner._prompt_refresh_fn is None

    # 即使库里新增了星标记忆，也不应被刷进来
    await _create_memory("库里的新星标", star_level="summary")

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_PATCH, AsyncMock(
            return_value=llmService.InferResult.success(_make_mock_response("摘要")),
        )),
        patch(_ACTIVITY_PATCH, _mock_activity_service()),
    ):
        await runner._execute_compact()

    assert runner.system_prompt == original_prompt


async def test_refresh_failure_does_not_break_compact(memory_db):
    """刷新抛异常不能把 compact 打挂（新 session 已建立，最坏只是沿用旧快照）。"""
    history = await _build_history()
    original_prompt = "旧 prompt"

    async def _broken_refresh() -> str:
        raise RuntimeError("模拟刷新失败")

    runner = _make_runner(original_prompt, prompt_refresh_fn=_broken_refresh)
    runner._history = history

    with (
        patch(_CONFIG_PATCH, return_value=_mock_config()),
        patch(_INFER_PATCH, AsyncMock(
            return_value=llmService.InferResult.success(_make_mock_response("摘要")),
        )),
        patch(_ACTIVITY_PATCH, _mock_activity_service()),
    ):
        await runner._execute_compact()  # 不应抛

    assert runner.system_prompt == original_prompt
    assert any(AgentHistoryTag.COMPACT_SUMMARY in item.tags for item in history)


# ---------------------------------------------------------------------------
# ④ HTTP 接口 + 清理联动
# ---------------------------------------------------------------------------


async def test_agent_memories_handler_returns_lowercase_enum(memory_db):
    """接口按 `AgentMemoriesHandler` 流程取数：枚举对外是小写 value。"""
    await _create_memory("概要星标", star_level="summary")
    await _create_memory("普通记忆")

    memories = await memoryService.get_agent_memories_for_view(_TEAM_ID, _AGENT_ID)
    payload = {"memories": [memory_api_dict(memory) for memory in memories]}

    assert len(payload["memories"]) == 2
    assert payload["memories"][0]["star_level"] == "summary"
    assert payload["memories"][1]["star_level"] == "none"
    assert payload["memories"][0]["status"] == "active"


async def test_agent_memories_handler_include_archived(memory_db):
    """`include_archived=true` 时归档记忆一并返回，其余情况不返回。"""
    await _create_memory("活跃记忆")
    archived = await memoryService.create_memory(
        _TEAM_ID, _AGENT_ID, title="归档记忆", summary="s", content="c",
    )
    await memoryService.update_memory(_TEAM_ID, _AGENT_ID, archived.id, status="archived")

    default_list = await memoryService.get_agent_memories_for_view(_TEAM_ID, _AGENT_ID)
    assert [memory.title for memory in default_list] == ["活跃记忆"]

    with_archived = await memoryService.get_agent_memories_for_view(
        _TEAM_ID, _AGENT_ID, include_archived=True,
    )
    assert {memory.title for memory in with_archived} == {"活跃记忆", "归档记忆"}


async def test_handler_rejects_unknown_agent(monkeypatch):
    """Agent 不存在时报 `agent_not_found`（与 AgentTasksHandler 同口径）。"""
    monkeypatch.setattr(
        agentController.gtAgentManager, "get_agents_by_ids", AsyncMock(return_value=[]),
    )
    service = AsyncMock()
    monkeypatch.setattr(agentController.memoryService, "get_agent_memories_for_view", service)

    handler = object.__new__(agentController.AgentMemoriesHandler)
    handler.get_query_argument = lambda name, default=None: default
    handler.return_json = lambda _data: None

    with pytest.raises(TogoException) as exc_info:
        await handler.get("99")

    assert exc_info.value.error_code == "agent_not_found"
    service.assert_not_called()


async def test_clear_agent_data_deletes_memories(memory_db):
    """`clear_agent_data` 联动清空该 Agent 的记忆，别的 Agent 不受影响。"""
    await _create_memory("我的记忆")
    await memoryService.create_memory(
        _TEAM_ID, 8, title="别人的记忆", summary="s", content="c",
    )

    agent = GtAgent(id=_AGENT_ID, team_id=_TEAM_ID, name="MemoryBot", role_template_id=1)
    team = SimpleNamespace(id=_TEAM_ID, name="测试团队")

    with (
        patch.object(teamService.gtAgentManager, "get_agent_by_id", AsyncMock(return_value=agent)),
        patch.object(teamService.gtTeamManager, "get_team_by_id", AsyncMock(return_value=team)),
        patch.object(teamService.gtAgentHistoryManager, "delete_history_by_agent", AsyncMock(return_value=0)),
        patch.object(teamService, "hot_reload_team", AsyncMock()),
    ):
        result = await teamService.clear_agent_data(_AGENT_ID)

    assert result["memories"] == 1
    assert await memoryService.get_agent_memories_for_view(_TEAM_ID, _AGENT_ID, include_archived=True) == []
    # 别人的记忆还在
    assert len(await memoryService.get_agent_memories_for_view(_TEAM_ID, 8)) == 1


async def test_clear_team_data_deletes_memories(memory_db):
    """`clear_team_data` 联动清空整个团队的记忆，其他团队不受影响。"""
    await _create_memory("本队记忆")
    await memoryService.create_memory(
        _TEAM_ID, 8, title="同队别人的", summary="s", content="c",
    )
    await memoryService.create_memory(
        99, _AGENT_ID, title="别队记忆", summary="s", content="c",
    )

    with (
        patch.object(teamService, "stop_team_runtime", AsyncMock()),
        patch.object(teamService.gtScheduleJobManager, "delete_jobs_by_team", AsyncMock(return_value=0)),
        patch.object(teamService.gtAgentTaskManager, "delete_tasks_by_team", AsyncMock(return_value=0)),
        patch.object(teamService.gtAgentHistoryManager, "delete_history_by_team", AsyncMock(return_value=0)),
        patch.object(teamService.gtRoomMessageManager, "delete_messages_by_team", AsyncMock(return_value=0)),
        patch.object(teamService.gtAgentActivityManager, "delete_activities_by_team", AsyncMock(return_value=0)),
        patch.object(teamService.gtRoomManager, "get_rooms_by_team", AsyncMock(return_value=[])),
        patch.object(teamService.gtRoomManager, "reset_room_runtime_state", AsyncMock()),
    ):
        result = await teamService.clear_team_data(_TEAM_ID)

    assert result["memories"] == 2
    assert await memoryService.get_agent_memories_for_view(_TEAM_ID, _AGENT_ID, include_archived=True) == []
    assert len(await memoryService.get_agent_memories_for_view(99, _AGENT_ID)) == 1
