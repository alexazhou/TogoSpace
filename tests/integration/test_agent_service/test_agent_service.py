"""integration tests for core behavior in service.agentService"""
import asyncio
import os
import sys
from unittest.mock import MagicMock

import pytest

from constants import AgentHistoryTag, DriverType, EmployStatus, MessageBusTopic, AgentStatus, AgentTaskStatus, AgentTaskType
from dal.db import gtAgentManager, gtTeamManager, gtAgentTaskManager
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtAgentHistory import GtAgentHistory
from model.dbModel.gtAgentTask import GtAgentTask
from service import presetService, agentService, roomService, ormService, persistenceService, messageBus
from service.agentService import promptBuilder
from util import configUtil, llmApiUtil
from ...base import ServiceTestCase

TEAM = "test_team"
_CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config")

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")



class _agentServiceCase(ServiceTestCase):
    """agentService 集成测试基类：统一加载测试专用 agent/team 配置。"""

    @classmethod
    async def async_setup_class(cls):
        db_path = cls._get_test_db_path()
        await ormService.startup(db_path)
        await persistenceService.startup()
        await roomService.startup()
        await presetService._import_role_templates_from_app_config()
        cfg = configUtil.load(_CONFIG_DIR, preset_dir=_CONFIG_DIR, force_reload=True)
        team_cfg = cfg.teams[0]
        await presetService._import_team_from_config(team_cfg)
        await agentService.startup()
        await agentService.load_all_team()

    @classmethod
    async def async_teardown_class(cls):
        await agentService.shutdown()
        roomService.shutdown()
        await persistenceService.shutdown()
        await ormService.shutdown()


class TestagentServiceCreateTeamAgents(_agentServiceCase):
    async def test_create_team_members(self):
        """create_team_members 后，team 维度的 agent 实例应全部可检索。"""
        team = await gtTeamManager.get_team(TEAM)
        assert team is not None
        alice = await gtAgentManager.get_agent(team.id, "alice")
        bob = await gtAgentManager.get_agent(team.id, "bob")
        assert alice is not None
        assert bob is not None
        assert agentService.get_agent(alice.id) is not None
        assert agentService.get_agent(bob.id) is not None


class TestagentServiceGetAgentsInRoom(_agentServiceCase):
    async def test_get_agents_in_room(self):
        """get_agents 只返回房间成员，并保持成员集合正确。"""
        await roomService.ensure_room_record(TEAM, "general", ["alice", "bob"])
        room = roomService.get_room_by_key(f"general@{TEAM}")
        assert {a.gt_agent.name for a in agentService.get_room_agents(room.room_id)} == {"alice", "bob"}


class TestAgentServiceStatusMap(_agentServiceCase):
    async def test_get_team_runtime_status_map(self):
        """运行时状态查询应按 agent_id 返回 ACTIVE/IDLE/FAILED。"""
        team = await gtTeamManager.get_team(TEAM)
        assert team is not None
        gt_alice = await gtAgentManager.get_agent(team.id, "alice")
        assert gt_alice is not None
        alice = agentService.get_agent(gt_alice.id)
        status_map = agentService.get_team_runtime_status_map(team.id)
        assert status_map[alice.gt_agent.id] == AgentStatus.IDLE

        alice.task_consumer.status = AgentStatus.ACTIVE
        status_map = agentService.get_team_runtime_status_map(team.id)
        assert status_map[alice.gt_agent.id] == AgentStatus.ACTIVE

        alice.task_consumer.status = AgentStatus.FAILED
        status_map = agentService.get_team_runtime_status_map(team.id)
        assert status_map[alice.gt_agent.id] == AgentStatus.FAILED

        alice.task_consumer.status = AgentStatus.IDLE


class TestAgentServiceAgentStatusEvent(_agentServiceCase):
    async def test_agent_status_event_contains_real_team_id(self):
        """订阅 AGENT_STATUS_CHANGED，验证事件中的 gt_agent.team_id 正确。"""
        team = await gtTeamManager.get_team(TEAM)
        assert team is not None
        gt_alice = await gtAgentManager.get_agent(team.id, "alice")
        assert gt_alice is not None
        alice = agentService.get_agent(gt_alice.id)

        received_payloads: list[dict] = []

        def _on_agent_status(msg) -> None:
            received_payloads.append(dict(msg.payload))

        messageBus.subscribe(MessageBusTopic.AGENT_STATUS_CHANGED, _on_agent_status)
        try:
            # 无任务时也会经历 ACTIVE -> IDLE，并发布两次状态事件。
            await alice.task_consumer.consume()
            await asyncio.sleep(0)
        finally:
            messageBus.unsubscribe(MessageBusTopic.AGENT_STATUS_CHANGED, _on_agent_status)

        alice_events = [p for p in received_payloads if getattr(p.get("gt_agent"), "name", None) == "alice"]
        assert len(alice_events) >= 2

        active_event = next((p for p in alice_events if p.get("status") == AgentStatus.ACTIVE), None)
        idle_event = next((p for p in alice_events if p.get("status") == AgentStatus.IDLE), None)
        assert active_event is not None
        assert idle_event is not None

        assert active_event["gt_agent"].id == alice.gt_agent.id
        assert active_event["gt_agent"].team_id == team.id
        assert active_event["gt_agent"].team_id > 0

        assert idle_event["gt_agent"].id == alice.gt_agent.id
        assert idle_event["gt_agent"].team_id == team.id
        assert idle_event["gt_agent"].team_id > 0


class TestAgentServiceSystemPrompt(_agentServiceCase):
    async def test_system_prompt_contains_template_and_agent_name(self):
        """system_prompt 应显式包含模板名称与 Agent 名称，便于模型识别身份。"""
        team = await gtTeamManager.get_team(TEAM)
        assert team is not None
        gt_alice = await gtAgentManager.get_agent(team.id, "alice")
        assert gt_alice is not None
        alice = agentService.get_agent(gt_alice.id)

        assert "你当前的名字：alice" in alice.system_prompt
        assert "你是身份：alice" in alice.system_prompt


class TestagentServiceGetAllRooms(_agentServiceCase):
    async def test_get_all_rooms_for_agent(self):
        """roomService.get_rooms_for_agent 应返回某个 agent 所在的所有 room_id。"""
        await roomService.ensure_room_record(TEAM, "general", ["alice"])
        room = roomService.get_room_by_key(f"general@{TEAM}")
        alice_id = room.get_agent_id_by_name("alice")
        assert room.room_id in roomService.get_rooms_for_agent(room.team_id, alice_id)


class TestagentServicePullRoomMessagesToHistory(_agentServiceCase):
    async def test_pull_room_messages_to_history(self):
        """pull_room_messages_to_history 会把房间中的新增消息拉取进 agent 历史。"""
        await roomService.ensure_room_record(TEAM, "general", ["alice", "bob"])
        room = roomService.get_room_by_key(f"general@{TEAM}")
        await room.activate_scheduling()
        bob_id = room.get_agent_id_by_name("bob")
        await room.add_message(bob_id, "hello alice")

        alice = agentService.get_agent(room.get_agent_id_by_name("alice"))
        synced_count = await alice.task_consumer._turn_runner.pull_room_messages_to_history(room)

        # 初始公告 + bob 消息会聚合成一条“轮到发言”上下文消息
        assert synced_count == 1
        assert len(alice.task_consumer._turn_runner._history) == 1
        content = alice.task_consumer._turn_runner._history[0].content or ""
        assert content.startswith("当前轮到你行动，房间名:【general】,新消息如下:")
        assert "【房间《general》】【系统提醒】：" in content
        assert "【房间《general》】【bob】：" in content
        assert "： hello alice" in content
        assert "你现在可以调用工具行动。" in content
        assert alice.task_consumer._turn_runner._history[0].tags == [AgentHistoryTag.ROOM_TURN_BEGIN]

    async def test_pull_room_messages_to_history_appends_complete_turn_prompt_as_last_history(self):
        """pull_room_messages_to_history 追加到 history 的最后一条必须是完整 turn prompt。"""
        await roomService.ensure_room_record(TEAM, "general", ["alice", "bob"])
        room = roomService.get_room_by_key(f"general@{TEAM}")
        await room.activate_scheduling()
        bob_id = room.get_agent_id_by_name("bob")
        await room.add_message(bob_id, "hello alice")

        alice = agentService.get_agent(room.get_agent_id_by_name("alice"))
        existing = llmApiUtil.OpenAIMessage.text(llmApiUtil.OpenaiApiRole.USER, "older context")
        item = GtAgentHistory.build(existing)
        item.agent_id = alice.gt_agent.id
        item.seq = 0
        alice.inject_history_messages([item])

        synced_count = await alice.task_consumer._turn_runner.pull_room_messages_to_history(room)

        system_line = promptBuilder.format_room_message("general", "SYSTEM", room.build_initial_system_message())
        bob_line = promptBuilder.format_room_message("general", "bob", "hello alice")
        expected_prompt = promptBuilder.build_turn_begin_prompt("general", [system_line, bob_line])

        assert synced_count == 1
        assert len(alice.task_consumer._turn_runner._history) == 2
        assert alice.task_consumer._turn_runner._history[-1].content == expected_prompt
        assert alice.task_consumer._turn_runner._history[-1].tags == [AgentHistoryTag.ROOM_TURN_BEGIN]
        assert alice.task_consumer._turn_runner._history[0].content == "older context"
        assert alice.task_consumer._turn_runner._history[0].tags == []


class TestSaveTeamAgentsFullReplace(_agentServiceCase):
    async def test_preserves_employee_numbers_when_updating_multiple_existing_agents(self):
        """全量保存多个已有成员时，应保留原有工号，避免唯一约束冲突。"""
        team = await gtTeamManager.get_team(TEAM)
        assert team is not None

        before_agents = await gtAgentManager.get_agents_by_employ_status(
            team.id,
            EmployStatus.ON_BOARD,
        )
        before_by_name = {agent.name: agent for agent in before_agents}
        assert {"alice", "bob"}.issubset(before_by_name)

        payload = [
            GtAgent(
                id=before_by_name["alice"].id,
                team_id=team.id,
                name="alice",
                role_template_id=before_by_name["alice"].role_template_id,
                model="gpt-4o",
                driver=DriverType.NATIVE,
            ),
            GtAgent(
                id=before_by_name["bob"].id,
                team_id=team.id,
                name="bob",
                role_template_id=before_by_name["bob"].role_template_id,
                model="gpt-4.1",
                driver=DriverType.NATIVE,
            ),
        ]

        saved_agents = await agentService.overwrite_team_agents(team.id, payload)
        saved_by_name = {agent.name: agent for agent in saved_agents}

        assert saved_by_name["alice"].employee_number == before_by_name["alice"].employee_number
        assert saved_by_name["bob"].employee_number == before_by_name["bob"].employee_number
        assert saved_by_name["alice"].model == "gpt-4o"
        assert saved_by_name["bob"].model == "gpt-4.1"


class TestagentServiceSyncSkipsOwnMessages(_agentServiceCase):
    async def test_sync_room_skips_own_messages(self):
        """同步时应过滤 agent 自己发过的消息，避免历史自回灌。"""
        await roomService.ensure_room_record(TEAM, "general", ["alice"])
        room = roomService.get_room_by_key(f"general@{TEAM}")
        await room.activate_scheduling()

        alice = agentService.get_agent(room.get_agent_id_by_name("alice"))
        alice_id = room.get_agent_id_by_name("alice")
        await room.add_message(alice_id, "i am talking")

        synced_count = await alice.task_consumer._turn_runner.pull_room_messages_to_history(room)
        # 只应有初始公告，不应有自己的消息
        assert synced_count == 1
        assert len(alice.task_consumer._turn_runner._history) == 1
        assert "talking" not in alice.task_consumer._turn_runner._history[0].content


class TestAgentResumeFailed(_agentServiceCase):
    async def test_resume_failed_marks_task_running_and_restarts_consumer(self):
        """FAILED 状态的 Agent 恢复时，应将最早失败任务转为 RUNNING 并重启统一执行流程。"""
        await roomService.ensure_room_record(TEAM, "resume_room", ["alice"])
        room = roomService.get_room_by_key(f"resume_room@{TEAM}")
        alice = agentService.get_agent(room.get_agent_id_by_name("alice"))

        failed_task = await gtAgentTaskManager.create_task(
            alice.gt_agent.id,
            AgentTaskType.ROOM_MESSAGE,
            {"room_id": room.room_id},
        )
        await gtAgentTaskManager.update_task_status(
            failed_task.id,
            AgentTaskStatus.FAILED,
            error_message="boom",
        )
        alice.task_consumer.status = AgentStatus.FAILED
        restart_spy = MagicMock()
        alice.task_consumer.start = restart_spy

        await alice.resume_failed()
        refreshed_task = await GtAgentTask.aio_get_or_none(GtAgentTask.id == failed_task.id)

        assert refreshed_task is not None
        assert refreshed_task.id == failed_task.id
        assert refreshed_task.status == AgentTaskStatus.RUNNING
        restart_spy.assert_called_once()
