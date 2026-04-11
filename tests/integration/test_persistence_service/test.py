import os
import sys
from pathlib import Path

import pytest

from constants import AgentStatus, AgentTaskStatus, AgentTaskType
from dal.db import gtTeamManager, gtAgentManager, gtAgentHistoryManager, gtAgentTaskManager
from model.dbModel.gtAgentHistory import GtAgentHistory
from model.dbModel.gtAgentTask import GtAgentTask
from model.dbModel.gtDept import GtDept
from model.dbModel.gtTeam import GtTeam
from service import presetService, agentService, ormService, persistenceService, roomService, messageBus, deptService
from service.agentService import Agent
from util import configUtil
from util.llmApiUtil import OpenAIMessage, OpenaiApiRole
from util.configTypes import TeamConfig, AgentConfig, TeamRoomConfig, DeptNodeConfig
from ...base import ServiceTestCase

TEAM = "test_team"
TEAMS_CONFIG = [TeamConfig(
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
    preset_rooms=[TeamRoomConfig(name="r1", agents=["alice", "bob"], max_turns=3)],
)]

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")



class TestRestoreRoomHistory(ServiceTestCase):
    """重启后 restore_runtime_state 能恢复房间消息历史和已读游标。"""

    db_path: Path = None

    @classmethod
    async def async_setup_class(cls):
        cls.db_path = Path(cls._get_test_db_path())
        await persistenceService.shutdown()
        await ormService.shutdown()
        roomService.shutdown()
        await messageBus.startup()
        await ormService.startup(str(cls.db_path))
        await persistenceService.startup()
        await roomService.startup()
        await presetService._import_role_templates_from_app_config()
        team = await gtTeamManager.save_team(GtTeam(name=TEAM))
        configs = [
            AgentConfig(name="alice", role_template="alice"),
            AgentConfig(name="bob", role_template="bob"),
        ]
        agents = await ServiceTestCase.convert_to_gt_agents(team.id, configs)
        await gtAgentManager.batch_save_agents(team.id, agents)
        await ServiceTestCase.create_room(TEAM, "r1", ["alice", "bob"], max_turns=3)
        room = roomService.get_room_by_key(f"r1@{TEAM}")
        await room.activate_scheduling()
        alice_id = room.get_agent_id_by_name("alice")
        bob_id = room.get_agent_id_by_name("bob")
        await room.add_message(alice_id, "hello")
        await room.get_unread_messages(bob_id)
        await room.add_message(bob_id, "world")
        await room.get_unread_messages(alice_id)

        # 模拟进程重启：关闭再重新打开同一 DB
        await persistenceService.shutdown()
        await ormService.shutdown()
        roomService.shutdown()

        await ormService.startup(str(cls.db_path))
        await persistenceService.startup()
        await roomService.startup()
        await roomService.load_all_rooms()
        cls.restored = roomService.get_room_by_key(f"r1@{TEAM}")
        await roomService.restore_all_rooms_runtime_state()

    @classmethod
    async def async_teardown_class(cls):
        await messageBus.shutdown()
        await presetService.shutdown()
        await persistenceService.shutdown()
        await ormService.shutdown()
        roomService.shutdown()

    async def test_messages_restored(self):
        assert [m.content for m in self.restored.messages] == [
            "系统提示: r1 房间已经创建，当前房间成员：alice、bob",
            "hello",
            "world",
        ]

    async def test_read_index_restored(self):
        assert self.restored.export_agent_read_index()["alice"] == 3
        assert self.restored.export_agent_read_index()["bob"] == 2



class TestRestoreAgentHistory(ServiceTestCase):
    """重启后 restore_runtime_state 能恢复 Agent 对话历史。"""

    db_path: Path = None
    running_task_id: int | None = None

    @classmethod
    async def async_setup_class(cls):
        cls.db_path = Path(cls._get_test_db_path())
        await persistenceService.shutdown()
        await ormService.shutdown()
        await agentService.shutdown()
        roomService.shutdown()
        await messageBus.startup()
        await ormService.startup(str(cls.db_path))
        await persistenceService.startup()
        await agentService.startup()
        await presetService._import_role_templates_from_app_config()
        configUtil.load(os.path.join(os.path.dirname(__file__), "../../config"), force_reload=True)
        team = await gtTeamManager.save_team(GtTeam(name=TEAM))
        agents = await ServiceTestCase.convert_to_gt_agents(
            team.id,
            [
                AgentConfig(name="alice", role_template="alice"),
                AgentConfig(name="bob", role_template="bob"),
            ],
        )
        await gtAgentManager.batch_save_agents(team.id, agents)
        gt_alice = await gtAgentManager.get_agent(team.id, "alice")
        gt_bob = await gtAgentManager.get_agent(team.id, "bob")
        assert gt_alice is not None
        assert gt_bob is not None
        await deptService.overwrite_dept_tree(
            team.id,
            GtDept(
                name="研发部",
                responsibility="负责协作与开发",
                manager_id=gt_alice.id,
                agent_ids=[gt_alice.id, gt_bob.id],
            ),
        )
        await gtAgentHistoryManager.append_agent_history_message(
            GtAgentHistory(
                agent_id=gt_alice.id,
                seq=0,
                role=OpenaiApiRole.USER,
                message=OpenAIMessage.text(OpenaiApiRole.USER, "u1"),
            )
        )
        await gtAgentHistoryManager.append_agent_history_message(
            GtAgentHistory(
                agent_id=gt_alice.id,
                seq=1,
                role=OpenaiApiRole.ASSISTANT,
                message=OpenAIMessage.text(OpenaiApiRole.ASSISTANT, "a1"),
            )
        )
        running_task = await gtAgentTaskManager.create_task(
            gt_alice.id,
            AgentTaskType.ROOM_MESSAGE,
            {"room_id": 1},
        )
        await gtAgentTaskManager.update_task_status(running_task.id, AgentTaskStatus.RUNNING)
        cls.running_task_id = running_task.id

        # 模拟进程重启
        await persistenceService.shutdown()
        await ormService.shutdown()
        await agentService.shutdown()

        await ormService.startup(str(cls.db_path))
        await persistenceService.startup()
        configUtil.load(os.path.join(os.path.dirname(__file__), "../../config"), force_reload=True)
        await presetService._import_role_templates_from_app_config()
        await agentService.startup()
        await agentService.load_all_team_agents()
        cls.fresh_agent = agentService.get_agent(gt_alice.id)
        await agentService.restore_all_agents_runtime_state()

    @classmethod
    async def async_teardown_class(cls):
        await messageBus.shutdown()
        await agentService.shutdown()
        await persistenceService.shutdown()
        await ormService.shutdown()

    async def test_history_restored(self):
        assert [m.content for m in self.fresh_agent.task_consumer._turn_runner._history] == ["u1", "a1"]

    async def test_running_task_marked_failed_after_restore(self):
        assert self.running_task_id is not None
        task = await GtAgentTask.aio_get_or_none(GtAgentTask.id == self.running_task_id)
        assert task is not None
        assert task.status == AgentTaskStatus.FAILED
        assert task.error_message == "task interrupted by process restart"

    async def test_agent_runtime_status_marked_failed_after_restore(self):
        assert self.fresh_agent.status == AgentStatus.FAILED
