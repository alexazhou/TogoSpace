"""integration tests for ClaudeSdkAgentDriver send/skip routing behavior"""
import os
import sys
from unittest.mock import patch

import pytest

from dal.db import gtTeamManager
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtAgentHistory import GtAgentHistory
from model.dbModel.gtAgentTask import GtAgentTask
from service import roomService, agentService, ormService, persistenceService
from service import funcToolService, presetService
from service.agentService import Agent
from service.agentService import promptBuilder
from service.agentService.driver.claudeSdkDriver import ClaudeSdkAgentDriver
from service.agentService.driver.base import AgentDriverConfig
from service.funcToolService.tools import FUNCTION_REGISTRY
from constants import DriverType, RoleTemplateType, AgentTaskType, SpecialAgent
from util import llmApiUtil, configUtil
from util.configTypes import TeamConfig, AgentConfig, DeptNodeConfig
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
        await funcToolService.startup()
        await presetService._import_role_templates_from_app_config()
        await agentService.startup()
        
        cfg = TeamConfig(
            name=TEAM,
            agents=[
                AgentConfig(name="alice", role_template="alice"),
                AgentConfig(name="bob", role_template="bob"),
            ],
            dept_tree=DeptNodeConfig(
                dept_name="研发部",
                responsibility="负责协作与开发",
                manager="alice",
                agents=["alice", "bob"],
            ),
        )
        await presetService._import_team_from_config(cfg)
        await agentService.load_all_team_agents()

    @classmethod
    async def async_teardown_class(cls):
        await agentService.shutdown()
        funcToolService.shutdown()
        roomService.shutdown()
        await persistenceService.shutdown()
        await ormService.shutdown()

    async def _make_driver_with_room(self, agent_name: str, current_room_name: str):
        """创建房间并从服务获取 agent，模拟调度器注入当前任务上下文的行为。"""
        # 1. roomService 处理持久化和成员关系
        await self.create_room(TEAM, current_room_name, [agent_name])
        room = roomService.get_room_by_key(f"{current_room_name}@{TEAM}")
        await room.activate_scheduling()

        # 2. 从 agentService 获取在内存中已注册好的 agent
        agent = agentService.get_agent(agentService.get_agent_id_by_stable_name(room.team_id, agent_name))

        # 3. 模拟 schedulerService：进入该房间回合前注入运行时的 current_db_task
        task = GtAgentTask(
            id=1,
            agent_id=agent.gt_agent.id,
            task_type=AgentTaskType.ROOM_MESSAGE,
            task_data={"room_id": room.room_id},
        )
        agent.task_consumer.current_db_task = task

        # 4. 模拟 TurnRunner.run_chat_turn 中设置的 _current_room 上下文
        agent.task_consumer._turn_runner._current_room = room

        # 5. 驱动绑定（不调 startup，手动注册 tool_registry）
        driver = ClaudeSdkAgentDriver(agent.task_consumer._turn_runner, AgentDriverConfig(driver_type="claude_sdk"))
        agent.task_consumer._turn_runner.tool_registry.clear()
        for t in funcToolService.get_tools_by_names(["send_chat_msg", "finish_chat_turn"]):
            fn_name = t.function.name
            agent.task_consumer._turn_runner.tool_registry.register(
                t,
                funcToolService.run_tool_call,
                marks_turn_finish=fn_name == "finish_chat_turn",
            )
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
        await self.create_room(TEAM, "group", ["alice"])
        await driver._build_claude_sdk_tool("send_chat_msg").handler({"room_name": "group", "msg": "hello group"})
        assert not driver._turn_done

    async def test_send_cross_room_lands_in_target(self):
        """跨房间消息应出现在目标房间，而非当前房间。"""
        driver, agent, current_room = await self._make_driver_with_room("alice", "private")
        await self.create_room(TEAM, "group", ["alice"])
        group = roomService.get_room_by_key(f"group@{TEAM}")
        await driver._build_claude_sdk_tool("send_chat_msg").handler({"room_name": "group", "msg": "hello group"})
        assert any(m.content == "hello group" for m in group.messages)
        assert not any(m.content == "hello group" for m in current_room.messages)

    async def test_send_cross_room_result_prompts_to_reply_current(self):
        """跨房间发言后，结果应提示 agent 还需回复当前房间。"""
        driver, agent, current_room = await self._make_driver_with_room("alice", "private")
        await self.create_room(TEAM, "group", ["alice"])
        result = await driver._build_claude_sdk_tool("send_chat_msg").handler({"room_name": "group", "msg": "hi"})
        text = result["content"][0]["text"]
        assert "消息已发送达 group" in text
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
        await self.create_room(TEAM, "lobby", ["alice"])
        room = roomService.get_room_by_key(f"lobby@{TEAM}")
        agent = Agent(
            gt_agent=GtAgent(id=1, team_id=1, name="alice", role_template_id=1, model="test-model"),
            system_prompt="test",
            driver_config=AgentDriverConfig(driver_type="native"),
        )
        driver = ClaudeSdkAgentDriver(agent.task_consumer._turn_runner, AgentDriverConfig(driver_type="claude_sdk"))
        task = GtAgentTask(
            id=1,
            agent_id=1,
            task_type=AgentTaskType.ROOM_MESSAGE,
            task_data={"room_id": room.room_id},
        )

        try:
            await driver.run_chat_turn(task, synced_count=0)
            assert False, "expected RuntimeError"
        except RuntimeError as exc:
            assert "尚未初始化" in str(exc)

    async def test_startup_without_allowed_tools_opens_all_local_tools_and_omits_sdk_allowlist(self):
        agent = Agent(
            gt_agent=GtAgent(id=1, team_id=1, name="alice", role_template_id=1, model="test-model"),
            system_prompt="test",
            driver_config=AgentDriverConfig(driver_type="native"),
        )
        driver = ClaudeSdkAgentDriver(agent.task_consumer._turn_runner, AgentDriverConfig(driver_type="claude_sdk"))

        captured_options = {}

        class _FakeClaudeClient:
            def __init__(self, options):
                captured_options.update(options)

            async def connect(self) -> None:
                return None

            async def disconnect(self) -> None:
                return None

        with patch("service.agentService.driver.claudeSdkDriver.create_sdk_mcp_server", return_value=object()), \
             patch("service.agentService.driver.claudeSdkDriver.ClaudeAgentOptions", side_effect=lambda **kwargs: kwargs), \
             patch("service.agentService.driver.claudeSdkDriver.ClaudeSDKClient", side_effect=_FakeClaudeClient):
            await driver.startup()

        exported_names = [tool.function.name for tool in agent.task_consumer._turn_runner.tool_registry.export_openai_tools()]
        assert set(exported_names) == set(FUNCTION_REGISTRY.keys())
        assert "allowed_tools" not in captured_options

    async def test_startup_with_allowed_tools_keeps_local_tools_narrow_and_passes_sdk_allowlist(self):
        agent = Agent(
            gt_agent=GtAgent(id=1, team_id=1, name="alice", role_template_id=1, model="test-model"),
            system_prompt="test",
            driver_config=AgentDriverConfig(driver_type="native"),
        )
        driver = ClaudeSdkAgentDriver(
            agent.task_consumer._turn_runner,
            AgentDriverConfig(driver_type="claude_sdk", options={"allowed_tools": ["Read"]}),
        )

        captured_options = {}

        class _FakeClaudeClient:
            def __init__(self, options):
                captured_options.update(options)

            async def connect(self) -> None:
                return None

            async def disconnect(self) -> None:
                return None

        with patch("service.agentService.driver.claudeSdkDriver.create_sdk_mcp_server", return_value=object()), \
             patch("service.agentService.driver.claudeSdkDriver.ClaudeAgentOptions", side_effect=lambda **kwargs: kwargs), \
             patch("service.agentService.driver.claudeSdkDriver.ClaudeSDKClient", side_effect=_FakeClaudeClient):
            await driver.startup()

        exported_names = [tool.function.name for tool in agent.task_consumer._turn_runner.tool_registry.export_openai_tools()]
        assert exported_names == ["send_chat_msg", "finish_chat_turn"]
        assert captured_options["allowed_tools"] == ["Read"]

    async def test_run_chat_turn_uses_max_retries_as_failed_action_retry_limit(self):
        await self.create_room(TEAM, "lobby", ["alice"])
        room = roomService.get_room_by_key(f"lobby@{TEAM}")
        agent = Agent(
            gt_agent=GtAgent(id=1, team_id=1, name="alice", role_template_id=1, model="test-model"),
            system_prompt="test",
            driver_config=AgentDriverConfig(driver_type="native"),
        )
        task = GtAgentTask(
            id=1,
            agent_id=1,
            task_type=AgentTaskType.ROOM_MESSAGE,
            task_data={"room_id": room.room_id},
        )
        agent.task_consumer.current_db_task = task
        agent.task_consumer._turn_runner._current_room = room
        driver = ClaudeSdkAgentDriver(agent.task_consumer._turn_runner, AgentDriverConfig(driver_type="claude_sdk"))
        fake_client = _FakeClaudeClient()
        driver._sdk_client = fake_client

        with patch("service.agentService.driver.claudeSdkDriver._RUN_CHAT_TURN_MAX_RETRIES", 1):
            with pytest.raises(RuntimeError, match="SDK 达到失败行动重试上限仍未完成行动"):
                await driver.run_chat_turn(task, synced_count=0)

        assert len(fake_client.queries) == 2

    async def test_run_chat_turn_prompt_has_context_wrappers_and_blank_lines(self):
        await self.create_room(TEAM, "lobby", ["alice", "bob"])
        room = roomService.get_room_by_key(f"lobby@{TEAM}")
        agent = Agent(
            gt_agent=GtAgent(id=1, team_id=1, name="alice", role_template_id=1, model="test-model"),
            system_prompt="test",
            driver_config=AgentDriverConfig(driver_type="native"),
        )
        task = GtAgentTask(
            id=1,
            agent_id=1,
            task_type=AgentTaskType.ROOM_MESSAGE,
            task_data={"room_id": room.room_id},
        )
        driver = ClaudeSdkAgentDriver(agent.task_consumer._turn_runner, AgentDriverConfig(driver_type="claude_sdk"))
        fake_client = _FakeClaudeClient()
        driver._sdk_client = fake_client

        # 使用 YAML 格式构建 turn prompt
        turn_prompt = promptBuilder.build_turn_begin_prompt("lobby", [
            ("系统提醒", "房间初始化"),
            ("bob", "hello alice"),
        ])
        item = GtAgentHistory.build(
            llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, turn_prompt),
        )
        item.agent_id = agent.gt_agent.id
        item.seq = 0
        agent.inject_history_messages([item])

        with patch("service.agentService.driver.claudeSdkDriver._RUN_CHAT_TURN_MAX_RETRIES", 0):
            with pytest.raises(RuntimeError, match="SDK 达到失败行动重试上限仍未完成行动"):
                await driver.run_chat_turn(task, synced_count=1)

        assert len(fake_client.queries) == 1
        first_prompt = fake_client.queries[0]
        assert "当前轮到你行动" in first_prompt
        assert "roomName: lobby" in first_prompt
        assert "sender: 系统提醒" in first_prompt
        assert "sender: bob" in first_prompt
        assert "content: 房间初始化" in first_prompt
        assert "content: hello alice" in first_prompt
        assert "你现在可以调用工具行动。" in first_prompt
