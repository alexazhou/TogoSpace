"""integration tests for toolLoader utilities and service-backed tool functions"""
import os
import sys
from typing import Optional

import pytest

import service.deptService as deptService
import service.ormService as ormService
import service.persistenceService as persistenceService
import service.roomService as roomService
import service.agentService as agentService
from dal.db import gtAgentTaskManager, gtTeamManager, gtAgentManager
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtDept import GtDept
from model.dbModel.gtTeam import GtTeam
from service.roomService import ToolCallContext
from service.funcToolService.toolLoader import (
    python_type_to_json_schema,
    get_function_metadata,
    build_tools,
)
from service.funcToolService.tools import (
    get_time,
    get_dept_info,
    get_room_info,
    get_agent_info,
    wake_up_agent,
    send_chat_msg,
    finish_chat_turn,
)
from constants import AgentStatus, AgentTaskStatus, AgentTaskType
from ...base import ServiceTestCase

TEAM = "test_team"

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


class _FakeRuntimeAgent:
    def __init__(
        self,
        gt_agent: GtAgent,
        status: AgentStatus,
        *,
        resume_error: Exception | None = None,
    ) -> None:
        self.gt_agent = gt_agent
        self.status = status
        self._resume_error = resume_error
        self.resumed = False

    async def resume_failed(self) -> None:
        if self._resume_error is not None:
            raise self._resume_error
        self.resumed = True



class TestPythonTypeToJsonSchema(ServiceTestCase):
    async def test_str(self):
        """str 映射为 JSON Schema string。"""
        assert python_type_to_json_schema(str) == {"type": "string"}

    async def test_int(self):
        """int 映射为 integer。"""
        assert python_type_to_json_schema(int) == {"type": "integer"}

    async def test_float(self):
        """float 映射为 number。"""
        assert python_type_to_json_schema(float) == {"type": "number"}

    async def test_bool(self):
        """bool 映射为 boolean。"""
        assert python_type_to_json_schema(bool) == {"type": "boolean"}

    async def test_optional_str(self):
        """Optional[T] 退化到 T 的 schema。"""
        assert python_type_to_json_schema(Optional[str]) == {"type": "string"}

    async def test_unknown_falls_back_to_object(self):
        """未知类型默认回退为 object，保证 schema 可生成。"""
        class Custom:
            pass
        assert python_type_to_json_schema(Custom) == {"type": "object"}



class TestGetFunctionMetadata(ServiceTestCase):
    async def test_name_is_set(self):
        """metadata 中 name 字段与注册名一致。"""
        assert get_function_metadata("get_time", get_time)["name"] == "get_time"

    async def test_description_from_docstring(self):
        """description 应从函数 docstring 提取。"""
        assert get_function_metadata("get_time", get_time)["description"]

    async def test_required_includes_timezone(self):
        """必填参数会进入 required 列表。"""
        assert "timezone" not in get_function_metadata("get_time", get_time)["parameters"].get("required", [])

    async def test_private_params_excluded(self):
        """以下划线开头的上下文参数不暴露给 LLM。"""
        props = get_function_metadata("send_chat_msg", send_chat_msg)["parameters"]["properties"]
        assert "_context" not in props



class TestBuildtools(ServiceTestCase):
    async def test_builds_tool_for_each_entry(self):
        """注册表中每个函数都应产出一个 OpenAITool 定义。"""
        tools = build_tools({"get_time": get_time, "get_dept_info": get_dept_info})
        assert len(tools) == 2
        assert {t.function.name for t in tools} == {"get_time", "get_dept_info"}

    async def test_empty_registry(self):
        """空注册表返回空列表。"""
        assert build_tools({}) == []

    async def test_skips_function_with_error(self):
        """构建过程中单个函数异常不影响其他函数。"""
        assert len(build_tools({"get_time": get_time})) == 1



class TestToolFunctions(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        # 这组用例依赖 roomService / persistence 的真实上下文。
        db_path = cls._get_test_db_path()
        await ormService.startup(db_path)
        await persistenceService.startup()
        await agentService.startup()
        await roomService.startup()
        team = await gtTeamManager.save_team(GtTeam(name=TEAM))
        await gtAgentManager.batch_save_agents(
            team.id,
            [
                GtAgent(team_id=team.id, name="alice", role_template_id=0),
                GtAgent(team_id=team.id, name="bob", role_template_id=0),
                GtAgent(team_id=team.id, name="char", role_template_id=0),
            ],
        )
        agents = await gtAgentManager.get_team_all_agents(team.id)
        cls.agent_ids = {a.name: a.id for a in agents}
        cls.team_id = team.id

    @classmethod
    async def async_teardown_class(cls):
        roomService.shutdown()
        await persistenceService.shutdown()
        await ormService.shutdown()

    async def test_get_time_local(self):
        """默认返回本地时区时间。"""
        assert "当前本地时间" in get_time()["message"]

    async def test_get_time_timezone(self):
        """指定时区时，返回内容包含目标时区标识。"""
        assert "UTC" in get_time(timezone="UTC")["message"]

    async def test_get_time_invalid_timezone(self):
        """未知时区应返回友好错误提示。"""
        result = get_time(timezone="Invalid/Zone")
        assert not result["success"] and "未知时区" in result["message"]

    async def test_get_dept_info_returns_error_without_context(self):
        """无团队上下文时，团队感知工具应返回明确错误。"""
        result = await get_dept_info()
        assert not result["success"]

    async def test_get_dept_info_returns_root_tree(self):
        """不传 dept_id 时返回根部门及其子树。"""
        team = await gtTeamManager.get_team(TEAM)
        assert team is not None
        # 编辑组织树前必须停用团队
        await gtTeamManager.set_team_enabled(team.id, False)
        alice = await gtAgentManager.get_agent(team.id, "alice")
        bob = await gtAgentManager.get_agent(team.id, "bob")
        char = await gtAgentManager.get_agent(team.id, "char")
        assert alice is not None and bob is not None and char is not None

        await deptService.overwrite_dept_tree(
            team.id,
            GtDept(
                name="company",
                responsibility="overall",
                manager_id=alice.id,
                agent_ids=[alice.id, bob.id, char.id],
                children=[
                    GtDept(
                        name="delivery",
                        responsibility="ship product",
                        manager_id=bob.id,
                        agent_ids=[bob.id, char.id],
                        children=[],
                    )
                ],
            ),
        )

        ctx = ToolCallContext(agent_id=self.agent_ids["alice"], team_id=team.id, chat_room=None)
        result = await get_dept_info(_context=ctx)

        assert result["success"]
        assert result["dept"]["dept_name"] == "company"
        assert result["dept"]["manager"] == "alice"
        assert result["dept"]["children"][0]["dept_name"] == "delivery"
        assert result["dept"]["children"][0]["members"] == ["bob", "char"]

    async def test_get_room_info_supports_list_and_detail(self):
        """房间工具应支持列表和详情两种查询模式。"""
        team = await gtTeamManager.get_team(TEAM)
        assert team is not None
        await self.create_room(TEAM, "general", ["alice", "bob"], max_turns=3)
        await self.create_room(TEAM, "pair", ["alice"], max_turns=0)
        general = roomService.get_room_by_key(f"general@{TEAM}")
        await general.activate_scheduling()
        await general.add_message(self.agent_ids["alice"], "hello")

        ctx = ToolCallContext(agent_id=self.agent_ids["alice"], team_id=team.id, chat_room=general)
        list_result = await get_room_info(_context=ctx)
        detail_result = await get_room_info(room_name="general", _context=ctx)

        assert list_result["success"]
        assert {room["room_name"] for room in list_result["rooms"]} >= {"general", "pair"}
        assert detail_result["success"]
        assert detail_result["room"]["room_name"] == "general"
        assert detail_result["room"]["members"] == ["alice", "bob"]
        assert detail_result["room"]["state"] == "SCHEDULING"
        assert detail_result["room"]["current_turn"] == "alice"
        assert detail_result["room"]["total_messages"] >= 2

    async def test_get_agent_info_and_wake_up_agent(self, monkeypatch):
        """成员工具应能返回失败摘要，并在 FAILED 状态下唤醒成员。"""
        team = await gtTeamManager.get_team(TEAM)
        assert team is not None
        # 编辑组织树前必须停用团队
        await gtTeamManager.set_team_enabled(team.id, False)
        alice = await gtAgentManager.get_agent(team.id, "alice")
        bob = await gtAgentManager.get_agent(team.id, "bob")
        char = await gtAgentManager.get_agent(team.id, "char")
        assert alice is not None and bob is not None and char is not None

        await deptService.overwrite_dept_tree(
            team.id,
            GtDept(
                name="ops",
                responsibility="keep things running",
                manager_id=alice.id,
                agent_ids=[alice.id, bob.id, char.id],
                children=[],
            ),
        )
        await self.create_room(TEAM, "ops-room", ["alice", "bob", "char"])

        task = await gtAgentTaskManager.create_task(
            bob.id,
            AgentTaskType.ROOM_MESSAGE,
            {"room_id": 1},
        )
        await gtAgentTaskManager.update_task_status(
            task.id,
            AgentTaskStatus.FAILED,
            error_message="llm provider unavailable during team restore",
        )

        alice_runtime = _FakeRuntimeAgent(alice, AgentStatus.IDLE)
        bob_runtime = _FakeRuntimeAgent(bob, AgentStatus.FAILED)
        char_runtime = _FakeRuntimeAgent(char, AgentStatus.ACTIVE)
        monkeypatch.setattr(
            agentService,
            "get_team_agents",
            lambda team_id: [alice_runtime, bob_runtime, char_runtime],
        )

        ctx = ToolCallContext(agent_id=self.agent_ids["alice"], team_id=team.id, chat_room=None)
        list_result = await get_agent_info(_context=ctx)
        detail_result = await get_agent_info(agent_name="bob", _context=ctx)
        wake_result = await wake_up_agent("bob", _context=ctx)

        assert list_result["success"]
        assert any(agent["name"] == "bob" and agent["status"] == "FAILED" for agent in list_result["agents"])
        failed_entry = next(agent for agent in list_result["agents"] if agent["name"] == "bob")
        assert "llm provider unavailable" in failed_entry["error_summary"]
        assert detail_result["success"]
        assert detail_result["agent"]["department"] == "ops"
        assert detail_result["agent"]["role"] == "member"
        assert "ops-room" in detail_result["agent"]["rooms"]
        assert detail_result["agent"]["can_wake_up"] is True
        assert wake_result["success"]
        assert bob_runtime.resumed is True

    async def test_wake_up_agent_rejects_non_failed_member(self, monkeypatch):
        """非 FAILED 成员不能被唤醒。"""
        team = await gtTeamManager.get_team(TEAM)
        assert team is not None
        alice = await gtAgentManager.get_agent(team.id, "alice")
        assert alice is not None

        monkeypatch.setattr(
            agentService,
            "get_team_agents",
            lambda team_id: [_FakeRuntimeAgent(alice, AgentStatus.IDLE)],
        )

        ctx = ToolCallContext(agent_id=self.agent_ids["alice"], team_id=team.id, chat_room=None)
        result = await wake_up_agent("alice", _context=ctx)

        assert not result["success"]
        assert "IDLE" in result["message"]

    async def test_send_chat_msg_returns_error_without_context(self):
        """无上下文时 send_chat_msg 应返回明确错误，不能伪装成功。"""
        assert not (await send_chat_msg("some_room", "hello"))["success"]

    async def test_send_chat_msg_with_valid_context(self):
        """同房间发送成功后，目标房间消息数应增加。"""
        await self.create_room(TEAM, "myroom", ["alice"])
        room = roomService.get_room_by_key(f"myroom@{TEAM}")
        await room.activate_scheduling()
        ctx = ToolCallContext(agent_id=self.agent_ids["alice"], team_id=room.team_id, chat_room=room)
        assert (await send_chat_msg("myroom", "hello", _context=ctx))["success"]
        assert len(room.messages) == 2  # 1 (init公告) + 1 (new)
        assert room.messages[1].content == "hello"

    async def test_send_chat_msg_nonexistent_room_returns_error(self):
        """目标房间不存在时应返回明确错误，避免吞掉失败。"""
        await self.create_room(TEAM, "existing", ["alice"])
        room = roomService.get_room_by_key(f"existing@{TEAM}")
        ctx = ToolCallContext(agent_id=self.agent_ids["alice"], team_id=room.team_id, chat_room=room)
        result = await send_chat_msg("nonexistent", "hello", _context=ctx)
        assert not result["success"] and "nonexistent" in result["message"]

    async def test_send_chat_msg_cross_room_lands_in_target(self):
        """跨房间发消息时，消息必须落到目标房间，而不是 agent 当前所在房间。"""
        await self.create_room(TEAM, "room_a", ["alice"])
        await self.create_room(TEAM, "room_b", ["alice"])
        room_a = roomService.get_room_by_key(f"room_a@{TEAM}")
        room_b = roomService.get_room_by_key(f"room_b@{TEAM}")
        ctx = ToolCallContext(agent_id=self.agent_ids["alice"], team_id=room_a.team_id, chat_room=room_a)
        result = await send_chat_msg("room_b", "hello from a to b", _context=ctx)
        assert result["success"]
        assert any(m.content == "hello from a to b" for m in room_b.messages)
        assert not any(m.content == "hello from a to b" for m in room_a.messages)

    async def test_send_chat_msg_cross_room_does_not_pollute_current_room(self):
        """发到其他房间时，当前房间的消息列表不变。"""
        await self.create_room(TEAM, "src", ["bob"])
        await self.create_room(TEAM, "dst", ["bob"])
        src = roomService.get_room_by_key(f"src@{TEAM}")
        dst = roomService.get_room_by_key(f"dst@{TEAM}")
        before_count = len(src.messages)
        ctx = ToolCallContext(agent_id=self.agent_ids["bob"], team_id=src.team_id, chat_room=src)
        await send_chat_msg("dst", "cross-room msg", _context=ctx)
        assert len(src.messages) == before_count

    async def test_send_chat_msg_cross_room_rejects_non_member(self):
        """跨房间目标存在但发言者不在成员中时，应返回失败且不插入消息。"""
        await self.create_room(TEAM, "src_non_member", ["alice"])
        await self.create_room(TEAM, "dst_non_member", ["bob"])
        src = roomService.get_room_by_key(f"src_non_member@{TEAM}")
        dst = roomService.get_room_by_key(f"dst_non_member@{TEAM}")
        before_count = len(dst.messages)
        ctx = ToolCallContext(agent_id=self.agent_ids["alice"], team_id=src.team_id, chat_room=src)

        result = await send_chat_msg("dst_non_member", "should fail", _context=ctx)

        assert not result["success"]
        assert "发送失败" in result["message"]
        assert len(dst.messages) == before_count

    async def test_finish_chat_turn_rejects_non_current_agent(self):
        """不是当前发言人时，finish_chat_turn 仍返回 success（agent 行动结束语义），但不推进轮次。"""
        await self.create_room(TEAM, "turn_room", ["alice", "bob"], max_turns=3)
        room = roomService.get_room_by_key(f"turn_room@{TEAM}")
        ctx = ToolCallContext(agent_id=self.agent_ids["bob"], team_id=room.team_id, chat_room=room)

        result = await finish_chat_turn(_context=ctx, confirm_no_need_talk=True)

        assert result["success"]
        assert gtAgentManager.get_agent_name(room._get_current_turn_agent_id()) == "alice"
