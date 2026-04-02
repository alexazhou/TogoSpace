"""integration tests for ClaudeSdkAgentDriver send/skip routing behavior"""
import os
import sys

import pytest

from dal.db import gtTeamManager
from model.dbModel.gtAgentHistory import GtAgentHistory
from service import roomService, agentService, ormService, persistenceService
from service import presetService
from service.agentService import Agent
from service.agentService.driver.claudeSdkDriver import ClaudeSdkAgentDriver
from service.agentService.driver.base import AgentDriverConfig
from constants import DriverType, RoleTemplateType
from util import llmApiUtil
from util.chatMessageFormat import build_turn_context_prompt, format_room_message
from util.configTypes import TeamConfig, AgentConfig
from ...base import ServiceTestCase

TEAM = "test_team"

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")



class TestSdkDoSend(ServiceTestCase):
    """测试 ClaudeSdkAgentDriver._handle_claude_sdk_tool_call：当前房间 vs 跨房间发言的路由与 done 标记行为。"""

    @classmethod
    async def async_setup_class(cls):
        db_path = cls._get_test_db_path()
        await ormService.startup(db_path)
        await persistenceService.startup()
        await roomService.startup()
        await presetService._import_role_templates_from_app_config()
        await agentService.startup()
        
        cfg = TeamConfig(name=TEAM, members=[
            AgentConfig(name="alice", role_template="alice"),
            AgentConfig(name="bob", role_template="bob")
        ])
        await presetService._import_team_from_config(cfg)
        await agentService.create_team_agents_from_db()

    @classmethod
    async def async_teardown_class(cls):
        await agentService.shutdown()
        roomService.shutdown()
        await persistenceService.shutdown()
        await ormService.shutdown()

    async def _make_driver_with_room(self, agent_name: str, current_room_name: str):
        """创建房间并从服务获取 agent，模拟调度器注入当前房间上下文的行为。"""
        # 1. roomService 处理持久化和成员关系
        await roomService.ensure_room_record(TEAM, current_room_name, [agent_name])
        room = roomService.get_room_by_key(f"{current_room_name}@{TEAM}")
        await room.activate_scheduling()
        
        # 2. 从 agentService 获取在内存中已注册好的 agent
        agent = agentService.get_team_agent(TEAM, agent_name)
        
        # 3. 模拟 schedulerService：进入该房间回合前注入运行时的 current_room
        agent.current_room = room
        
        # 4. 驱动绑定
        driver = ClaudeSdkAgentDriver(agent, AgentDriverConfig(driver_type="claude_sdk"))
        return driver, agent, room

    async def test_send_to_current_room_does_not_set_done(self):
        """发到当前房间后，本轮不应结束（_turn_done 应为 False）。"""
        driver, agent, room = await self._make_driver_with_room("alice", "lobby")
        await driver._build_claude_sdk_tool("send_chat_msg").handler({"room_name": "lobby", "msg": "hi everyone"})
        assert not driver._turn_done

    async def test_finish_chat_turn_sets_done(self):
        """调用 finish_chat_turn 后，本轮应结束（_turn_done 置 True）。"""
        driver, agent, room = await self._make_driver_with_room("alice", "lobby")
        await driver._build_claude_sdk_tool("finish_chat_turn").handler({})
        assert driver._turn_done

    async def test_send_to_current_room_message_appears(self):
        """发到当前房间的消息应出现在该房间里。"""
        driver, agent, room = await self._make_driver_with_room("alice", "lobby")
        await driver._build_claude_sdk_tool("send_chat_msg").handler({"room_name": "lobby", "msg": "hi everyone"})
        assert any(m.content == "hi everyone" for m in room.messages)

    async def test_send_to_current_room_result_prompts_to_finish(self):
        """发到当前房间时，返回结果应提示可以继续或调用 finish_chat_turn。"""
        driver, agent, room = await self._make_driver_with_room("alice", "lobby")
        result = await driver._build_claude_sdk_tool("send_chat_msg").handler({"room_name": "lobby", "msg": "hi"})
        assert "finish_chat_turn" in result["content"][0]["text"]

    async def test_send_cross_room_does_not_set_done(self):
        """发到其他房间时，不应结束当前轮次。"""
        driver, agent, current_room = await self._make_driver_with_room("alice", "private")
        await roomService.ensure_room_record(TEAM, "group", ["alice"])
        await driver._build_claude_sdk_tool("send_chat_msg").handler({"room_name": "group", "msg": "hello group"})
        assert not driver._turn_done

    async def test_send_cross_room_lands_in_target(self):
        """跨房间消息应出现在目标房间，而非当前房间。"""
        driver, agent, current_room = await self._make_driver_with_room("alice", "private")
        await roomService.ensure_room_record(TEAM, "group", ["alice"])
        group = roomService.get_room_by_key(f"group@{TEAM}")
        await driver._build_claude_sdk_tool("send_chat_msg").handler({"room_name": "group", "msg": "hello group"})
        assert any(m.content == "hello group" for m in group.messages)
        assert not any(m.content == "hello group" for m in current_room.messages)

    async def test_send_cross_room_result_prompts_to_reply_current(self):
        """跨房间发言后，结果应提示 agent 还需回复当前房间。"""
        driver, agent, current_room = await self._make_driver_with_room("alice", "private")
        await roomService.ensure_room_record(TEAM, "group", ["alice"])
        result = await driver._build_claude_sdk_tool("send_chat_msg").handler({"room_name": "group", "msg": "hi"})
        text = result["content"][0]["text"]
        assert current_room.name in text
        assert "本轮发言结束" not in text


class _FakeClaudeClient:
    def __init__(self):
        self.queries: list[str] = []

    async def query(self, prompt: str) -> None:
        self.queries.append(prompt)

    async def receive_response(self):
        if False:
            yield None

    async def interrupt(self) -> None:
        return None



class TestClaudeSdkAgentDriver(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls):
        db_path = cls._get_test_db_path()
        await ormService.startup(db_path)
        await persistenceService.startup()
        await roomService.startup()
        await presetService._import_role_templates_from_app_config()
        await presetService._import_team_from_config(TeamConfig(name=TEAM))
        await agentService.startup()

    @classmethod
    async def async_teardown_class(cls):
        await agentService.shutdown()
        roomService.shutdown()
        await persistenceService.shutdown()
        await ormService.shutdown()

    async def test_run_chat_turn_requires_started_client(self):
        await roomService.ensure_room_record(TEAM, "lobby", ["alice"])
        room = roomService.get_room_by_key(f"lobby@{TEAM}")
        agent = Agent(name="alice", team_name=TEAM, system_prompt="test", model="test-model",
                      driver_config=AgentDriverConfig(driver_type="native"))
        driver = ClaudeSdkAgentDriver(agent, AgentDriverConfig(driver_type="claude_sdk"))

        try:
            await driver.run_chat_turn(room, synced_count=0, max_function_calls=1)
            assert False, "expected RuntimeError"
        except RuntimeError as exc:
            assert "尚未初始化" in str(exc)

    async def test_run_chat_turn_uses_max_function_calls_as_retry_limit(self):
        await roomService.ensure_room_record(TEAM, "lobby", ["alice"])
        room = roomService.get_room_by_key(f"lobby@{TEAM}")
        agent = Agent(name="alice", team_name=TEAM, system_prompt="test", model="test-model",
                      driver_config=AgentDriverConfig(driver_type="native"))
        agent.current_room = room
        driver = ClaudeSdkAgentDriver(agent, AgentDriverConfig(driver_type="claude_sdk"))
        fake_client = _FakeClaudeClient()
        driver._sdk_client = fake_client

        await driver.run_chat_turn(room, synced_count=0, max_function_calls=2)

        assert len(fake_client.queries) == 2

        assert len(fake_client.queries) == 2

    async def test_run_chat_turn_prompt_has_context_wrappers_and_blank_lines(self):
        await roomService.ensure_room_record(TEAM, "lobby", ["alice", "bob"])
        room = roomService.get_room_by_key(f"lobby@{TEAM}")
        agent = Agent(
            name="alice",
            team_name=TEAM,
            system_prompt="test",
            model="test-model",
            driver_config=AgentDriverConfig(driver_type="native"),
        )
        driver = ClaudeSdkAgentDriver(agent, AgentDriverConfig(driver_type="claude_sdk"))
        fake_client = _FakeClaudeClient()
        driver._sdk_client = fake_client

        first = format_room_message("lobby", "SYSTEM", "房间初始化")
        second = format_room_message("lobby", "bob", "hello alice")
        turn_prompt = build_turn_context_prompt("lobby", [first, second])
        agent.inject_history_messages([
            GtAgentHistory.from_openai_message(
                agent.agent_id,
                0,
                llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiLLMApiRole.USER, turn_prompt),
            ),
        ])

        await driver.run_chat_turn(room, synced_count=1, max_function_calls=1)

        assert len(fake_client.queries) == 1
        first_prompt = fake_client.queries[0]
        assert "【lobby】 房间轮到你行动，新消息如下：" in first_prompt
        assert "你现在可以调用工具行动。" in first_prompt
        assert first in first_prompt
        assert second in first_prompt
        assert f"{first}\n\n{second}" in first_prompt
