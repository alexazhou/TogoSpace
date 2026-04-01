"""integration tests for core behavior in service.agentService"""
import asyncio
import os
import sys

import pytest

from constants import DriverType, EmployStatus, MessageBusTopic, MemberStatus
from dal.db import gtAgentManager, gtTeamManager
from model.dbModel.gtAgent import GtAgent
from service import presetService, agentService, roomService, ormService, persistenceService, messageBus
from util import configUtil
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
        cfg = configUtil.load(_CONFIG_DIR, force_reload=True)
        team_cfg = cfg.teams[0]
        await presetService._import_team_from_config(team_cfg)
        await agentService.startup()
        await agentService.create_team_agents_from_db()

    @classmethod
    async def async_teardown_class(cls):
        await agentService.shutdown()
        roomService.shutdown()
        await persistenceService.shutdown()
        await ormService.shutdown()


class TestagentServiceCreateTeamAgents(_agentServiceCase):
    async def test_create_team_members(self):
        """create_team_members 后，team 维度的 agent 实例应全部可检索。"""
        assert agentService.get_team_agent(TEAM, "alice") is not None
        assert agentService.get_team_agent(TEAM, "bob") is not None


class TestagentServiceGetAgentsInRoom(_agentServiceCase):
    async def test_get_agents_in_room(self):
        """get_agents 只返回房间成员，并保持成员集合正确。"""
        await roomService.ensure_room_record(TEAM, "general", ["alice", "bob"])
        room = roomService.get_room_by_key(f"general@{TEAM}")
        assert {a.name for a in agentService.get_team_agents(room.room_id)} == {"alice", "bob"}


class TestAgentServiceStatusMap(_agentServiceCase):
    async def test_get_team_agent_status_map(self):
        """运行时状态查询应按 agent_id 返回 ACTIVE/IDLE。"""
        alice = agentService.get_team_agent(TEAM, "alice")
        status_map = agentService.get_team_agent_status_map(TEAM)
        assert status_map[alice.agent_id] == MemberStatus.IDLE

        alice.status = MemberStatus.ACTIVE
        status_map = agentService.get_team_agent_status_map(TEAM)
        assert status_map[alice.agent_id] == MemberStatus.ACTIVE

        alice.status = MemberStatus.IDLE


class TestAgentServiceMemberStatusEvent(_agentServiceCase):
    async def test_member_status_event_contains_real_team_id(self):
        """订阅 MEMBER_STATUS_CHANGED，验证事件中的 team_id/team_name 正确。"""
        alice = agentService.get_team_agent(TEAM, "alice")
        team = await gtTeamManager.get_team(TEAM)
        assert team is not None

        received_payloads: list[dict] = []

        def _on_member_status(msg) -> None:
            received_payloads.append(dict(msg.payload))

        messageBus.subscribe(MessageBusTopic.MEMBER_STATUS_CHANGED, _on_member_status)
        try:
            # 无任务时也会经历 ACTIVE -> IDLE，并发布两次状态事件。
            await alice.consume_task(max_function_calls=1)
            await asyncio.sleep(0)
        finally:
            messageBus.unsubscribe(MessageBusTopic.MEMBER_STATUS_CHANGED, _on_member_status)

        alice_events = [p for p in received_payloads if p.get("member_name") == "alice"]
        assert len(alice_events) >= 2

        active_event = next((p for p in alice_events if p.get("status") == MemberStatus.ACTIVE.name), None)
        idle_event = next((p for p in alice_events if p.get("status") == MemberStatus.IDLE.name), None)
        assert active_event is not None
        assert idle_event is not None

        assert active_event["team_id"] == team.id
        assert active_event["team_id"] > 0
        assert active_event["team_name"] == TEAM

        assert idle_event["team_id"] == team.id
        assert idle_event["team_id"] > 0
        assert idle_event["team_name"] == TEAM


class TestAgentServiceSystemPrompt(_agentServiceCase):
    async def test_system_prompt_contains_template_and_member_name(self):
        """system_prompt 应显式包含模板名称与成员名称，便于模型识别身份。"""
        alice = agentService.get_team_agent(TEAM, "alice")

        assert "你当前的名字：alice" in alice.system_prompt
        assert "你是身份：alice" in alice.system_prompt


class TestagentServiceGetAllRooms(_agentServiceCase):
    async def test_get_all_rooms_for_agent(self):
        """get_all_rooms 应返回某个 agent 所在的所有 room_id。"""
        await roomService.ensure_room_record(TEAM, "general", ["alice"])
        room = roomService.get_room_by_key(f"general@{TEAM}")
        assert room.room_id in agentService.get_all_rooms(TEAM, "alice")


class TestagentServiceSyncRoomMessages(_agentServiceCase):
    async def test_sync_room_messages(self):
        """_sync_room_messages 会把房间中的新增消息同步进 agent 历史。"""
        await roomService.ensure_room_record(TEAM, "general", ["alice", "bob"])
        room = roomService.get_room_by_key(f"general@{TEAM}")
        await room.activate_scheduling()
        await room.add_message("bob", "hello alice")

        alice = agentService.get_team_agent(TEAM, "alice")
        synced_count = await alice.sync_room_messages(room)

        # 初始公告 + bob 消息会聚合成一条“轮到发言”上下文消息
        assert synced_count == 1
        assert len(alice._history) == 1
        content = alice._history[0].content or ""
        assert content.startswith("【general】 房间轮到你行动，新消息如下：")
        assert "【房间《general》】【系统提醒】：" in content
        assert "【房间《general》】【bob】：" in content
        assert "： hello alice" in content
        assert "你现在可以调用工具行动。" in content


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

        alice = agentService.get_team_agent(TEAM, "alice")
        await room.add_message("alice", "i am talking")

        synced_count = await alice.sync_room_messages(room)
        # 只应有初始公告，不应有自己的消息
        assert synced_count == 1
        assert len(alice._history) == 1
        assert "talking" not in alice._history[0].content
