"""integration tests for service.funcToolService — 需要 funcToolService.startup()"""
import json
import os
import sys
from dataclasses import replace
from unittest.mock import MagicMock

import pytest

import service.funcToolService as funcToolService
import service.ormService as ormService
import service.persistenceService as persistenceService
import service.roomService as roomService
from dal.db import gtTeamManager, gtAgentManager
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtTeam import GtTeam
from service.roomService import ToolCallContext
from ...base import ServiceTestCase

TEAM = "test_team"

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")



class TestfuncToolServiceInit(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        # 这组用例只验证工具注册生命周期，不依赖房间状态。
        await funcToolService.startup()

    async def test_init_loads_tools(self):
        """startup 后工具注册表应非空。"""
        assert len(funcToolService.get_tools()) > 0

    async def test_close_clears_tools(self):
        """shutdown 后工具注册表应被清空。"""
        funcToolService.shutdown()
        assert funcToolService.get_tools() == []



class TestRunToolCall(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        # send_chat_msg 依赖房间上下文，因此同时初始化 room + tool service。
        db_path = cls._get_test_db_path()
        await ormService.startup(db_path)
        await persistenceService.startup()
        await roomService.startup()
        team = await gtTeamManager.save_team(GtTeam(name=TEAM))
        await gtAgentManager.batch_save_agents(
            team.id,
            [GtAgent(team_id=team.id, name="alice", role_template_id=0), GtAgent(team_id=team.id, name="bob", role_template_id=0)],
        )
        agents = await gtAgentManager.get_team_all_agents(team.id)
        cls.agent_ids = {a.name: a.id for a in agents}
        cls.team_id = team.id
        await funcToolService.startup()

    @classmethod
    async def async_teardown_class(cls):
        funcToolService.shutdown()
        roomService.shutdown()
        await persistenceService.shutdown()
        await ormService.shutdown()

    async def _get_agent_id(self, name: str) -> int | None:
        gt_agent = await gtAgentManager.get_agent(self.team_id, name)
        return gt_agent.id if gt_agent else None

    async def _run(self, name, args, **kw):
        context = kw.get("context")
        if context is None:
            context = ToolCallContext(
                agent_id=1,
                team_id=1,
                chat_room=MagicMock(),
                tool_name=name,
            )
        else:
            context = replace(context, tool_name=name)
        return await funcToolService.run_tool_call(args, context=context)

    async def test_run_tool_call_basic(self):
        """正常 JSON 入参可成功执行工具函数。"""
        result = await self._run("get_time", '{"timezone": "UTC"}')
        assert result["success"] and "UTC" in result["message"]

    async def test_run_tool_call_invalid_json(self):
        """非法 JSON 不应抛异常，应返回可读错误文本。"""
        result = await self._run("send_chat_msg", "not json")
        assert not result["success"]

    async def test_run_tool_call_unknown_function(self):
        """未知函数名应返回失败信息。"""
        result = await self._run("nonexistent", "{}")
        assert not result["success"]

    async def test_run_tool_call_with_context(self):
        """上下文注入场景：send_chat_msg 能在上下文房间成功落消息。"""
        await self.create_room(TEAM, "ctx_room", ["alice"])
        room = roomService.get_room_by_key(f"ctx_room@{TEAM}")
        ctx = ToolCallContext(agent_id=self.agent_ids["alice"], team_id=room.team_id, chat_room=room)
        result = await self._run("send_chat_msg", '{"room_name": "ctx_room", "msg": "test"}', context=ctx)
        assert result["success"] and "消息已送达" in result["message"]

    async def test_run_tool_call_with_missing_room_returns_error(self):
        """目标房间不存在时，应返回错误信息。"""
        await self.create_room(TEAM, "ctx_room_missing", ["alice"])
        room = roomService.get_room_by_key(f"ctx_room_missing@{TEAM}")
        ctx = ToolCallContext(agent_id=self.agent_ids["alice"], team_id=room.team_id, chat_room=room)
        result = await self._run("send_chat_msg", '{"room_name": "missing_room", "msg": "test"}', context=ctx)
        assert not result["success"]
        assert not any(message.content == "test" for message in room.messages)

    async def test_run_tool_call_returns_false_when_sender_not_in_target_room(self):
        """tool 内部校验失败时，run_tool_call 应返回 success=false。"""
        await self.create_room(TEAM, "ctx_src", ["alice"])
        await self.create_room(TEAM, "ctx_dst", ["bob"])
        room = roomService.get_room_by_key(f"ctx_src@{TEAM}")
        target = roomService.get_room_by_key(f"ctx_dst@{TEAM}")
        before_count = len(target.messages)
        ctx = ToolCallContext(agent_id=self.agent_ids["alice"], team_id=room.team_id, chat_room=room)

        result = await self._run("send_chat_msg", '{"room_name": "ctx_dst", "msg": "test"}', context=ctx)

        assert not result["success"]
        assert len(target.messages) == before_count
