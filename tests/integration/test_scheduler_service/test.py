"""integration tests for service.schedulerService"""
import asyncio
import logging
import os
import pytest
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import service.roomService as roomService
import service.memberService as memberService
import service.schedulerService as scheduler
from service.memberService import TeamMember
from service.messageBus import Message
from model.coreModel.gtCoreAgentEvent import GtCoreRoomMessageEvent
from model.dbModel.gtRoom import GtRoom
from model.dbModel.gtTeam import GtTeam
from model.dbModel.gtTeamMember import GtTeamMember
from constants import MessageBusTopic, MemberStatus
from util.configTypes import TeamConfig
from ...base import ServiceTestCase

TEAM = "test_team"

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


def _make_mock_member(name: str, team_name: str = TEAM) -> TeamMember:
    """构造最小可运行的 TeamMember mock，用于观察 scheduler 调度行为。"""
    agent = MagicMock(spec=TeamMember)
    agent.name = name
    agent.team_name = team_name
    agent.key = f"{name}@{team_name}"
    agent.wait_task_queue = asyncio.Queue()
    agent.consume_task = AsyncMock()
    return agent


def _make_team_config() -> TeamConfig:
    return TeamConfig.model_validate({
        "name": TEAM,
        "members": [{"name": "alice", "agent": "alice"}],
        "preset_rooms": [{"name": "r1", "members": ["alice"], "max_turns": 1}],
    })


@pytest.mark.forked
class TestSchedulerRun(ServiceTestCase):
    def setup_method(self):
        # 清理可能残留的 scheduler 状态，避免测试间污染
        scheduler.shutdown()

    async def test_scheduler_run_terminates_on_stop(self):
        """调用 scheduler.shutdown() 后，scheduler.run() 应正常结束。"""
        await roomService.startup()
        await scheduler.startup([])
        run_task = asyncio.create_task(scheduler.run())
        await asyncio.sleep(0.1)
        scheduler.shutdown()
        await asyncio.wait_for(run_task, timeout=2.0)

    async def test_scheduler_runs_agent_on_turn_event(self):
        """发布 ROOM_MEMBER_TURN 后，scheduler 应触发 agent.consume_task。"""
        alice = _make_mock_member("alice")
        room = roomService.ChatRoom(
            team=GtTeam(id=1, name=TEAM),
            room=GtRoom(
                id=1,
                team_id=1,
                name="r1",
                type=roomService.RoomType.GROUP,
                initial_topic="",
                max_turns=0,
                member_read_index=None,
                updated_at=GtRoom._now(),
            ),
            members=[GtTeamMember(id=0, team_id=1, name="alice", agent_name="alice")],
        )

        teams_config = [_make_team_config()]
        await scheduler.startup(teams_config)

        with patch("service.schedulerService.memberService.get_team_member", return_value=alice):
            run_task = asyncio.create_task(scheduler.run())

            msg = Message(
                topic=MessageBusTopic.ROOM_MEMBER_TURN,
                payload={"member_name": "alice", "room_id": room.room_id, "room_name": "r1", "room_key": f"r1@{TEAM}", "team_name": TEAM},
            )
            scheduler._on_member_turn(msg)

            # consume_task 由后台任务异步消费队列，给一个短暂让渡时间。
            await asyncio.sleep(0.5)

            alice.consume_task.assert_called()

            scheduler.shutdown()
            await asyncio.wait_for(run_task, timeout=2.0)

    async def test_agent_is_active_self_contained(self):
        """验证 Agent 活跃状态的自治逻辑：基于 status 或 队列深度。"""
        alice = TeamMember("alice", TEAM, "prompt", "model")

        assert alice.is_active is False

        alice.wait_task_queue.put_nowait(GtCoreRoomMessageEvent(1))
        assert alice.is_active is True

        alice.wait_task_queue.get_nowait()
        alice.status = MemberStatus.ACTIVE
        assert alice.is_active is True

        alice.status = MemberStatus.IDLE
        assert alice.is_active is False

    async def test_handle_event_error_logged_in_agent(self):
        """验证 Agent.consume_task 内部错误不导致崩溃。"""
        real_agent = TeamMember("test", TEAM, "prompt", "model")
        real_agent.wait_task_queue.put_nowait(GtCoreRoomMessageEvent(1))

        with patch.object(real_agent, "run_chat_turn", side_effect=RuntimeError("boom")):
            await real_agent.consume_task(max_function_calls=5)

        # 即使 run_turn 报错，队列也应被正确消费，避免任务卡死。
        assert real_agent.wait_task_queue.empty()

    async def test_unsupported_task_type_is_logged(self, caplog):
        """不支持的任务类型应报错并记录日志，且不会卡住队列。"""
        real_agent = TeamMember("test", TEAM, "prompt", "model")
        real_agent.wait_task_queue.put_nowait(object())

        with caplog.at_level(logging.ERROR):
            await real_agent.consume_task(max_function_calls=5)

        assert real_agent.wait_task_queue.empty()
        assert "不支持的任务类型" in caplog.text

    async def test_on_agent_turn_creates_task(self):
        """收到 ROOM_MEMBER_TURN 消息后，agent 任务入队并启动 Task。"""
        alice = _make_mock_member("alice")
        room = roomService.ChatRoom(
            team=GtTeam(id=1, name=TEAM),
            room=GtRoom(
                id=1,
                team_id=1,
                name="r1",
                type=roomService.RoomType.GROUP,
                initial_topic="",
                max_turns=0,
                member_read_index=None,
                updated_at=GtRoom._now(),
            ),
            members=[GtTeamMember(id=0, team_id=1, name="alice", agent_name="alice")],
        )
        teams_config = [_make_team_config()]
        await scheduler.startup(teams_config)

        with patch("service.schedulerService.memberService.get_team_member", return_value=alice):
            msg = Message(
                topic=MessageBusTopic.ROOM_MEMBER_TURN,
                payload={"member_name": "alice", "room_id": room.room_id, "room_name": "r1", "room_key": f"r1@{TEAM}", "team_name": TEAM},
            )
            scheduler._on_member_turn(msg)

        assert not alice.wait_task_queue.empty()
        assert f"alice@{TEAM}" in scheduler._running

    async def test_duplicate_room_event_is_skipped(self):
        """同一房间连续触发两次 ROOM_MEMBER_TURN，队列中只应有一个事件。"""
        alice = _make_mock_member("alice")
        room = roomService.ChatRoom(
            team=GtTeam(id=1, name=TEAM),
            room=GtRoom(
                id=1,
                team_id=1,
                name="r1",
                type=roomService.RoomType.GROUP,
                initial_topic="",
                max_turns=0,
                member_read_index=None,
                updated_at=GtRoom._now(),
            ),
            members=[GtTeamMember(id=0, team_id=1, name="alice", agent_name="alice")],
        )
        teams_config = [_make_team_config()]
        await scheduler.startup(teams_config)

        with patch("service.schedulerService.memberService.get_team_member", return_value=alice):
            msg = Message(
                topic=MessageBusTopic.ROOM_MEMBER_TURN,
                payload={"member_name": "alice", "room_id": room.room_id, "room_name": "r1", "room_key": f"r1@{TEAM}", "team_name": TEAM},
            )
            scheduler._on_member_turn(msg)
            scheduler._on_member_turn(msg)

        assert alice.wait_task_queue.qsize() == 1

    async def test_different_rooms_not_deduplicated(self):
        """不同房间的事件不应被去重，各自独立入队。"""
        alice = _make_mock_member("alice")
        r1 = roomService.ChatRoom(
            team=GtTeam(id=1, name=TEAM),
            room=GtRoom(
                id=1,
                team_id=1,
                name="r1",
                type=roomService.RoomType.GROUP,
                initial_topic="",
                max_turns=0,
                member_read_index=None,
                updated_at=GtRoom._now(),
            ),
            members=[GtTeamMember(id=0, team_id=1, name="alice", agent_name="alice")],
        )
        r2 = roomService.ChatRoom(
            team=GtTeam(id=1, name=TEAM),
            room=GtRoom(
                id=2,
                team_id=1,
                name="r2",
                type=roomService.RoomType.GROUP,
                initial_topic="",
                max_turns=0,
                member_read_index=None,
                updated_at=GtRoom._now(),
            ),
            members=[GtTeamMember(id=0, team_id=1, name="alice", agent_name="alice")],
        )
        teams_config = [_make_team_config()]
        await scheduler.startup(teams_config)

        with patch("service.schedulerService.memberService.get_team_member", return_value=alice):
            msg_r1 = Message(
                topic=MessageBusTopic.ROOM_MEMBER_TURN,
                payload={"member_name": "alice", "room_id": r1.room_id, "room_name": "r1", "room_key": f"r1@{TEAM}", "team_name": TEAM},
            )
            msg_r2 = Message(
                topic=MessageBusTopic.ROOM_MEMBER_TURN,
                payload={"member_name": "alice", "room_id": r2.room_id, "room_name": "r2", "room_key": f"r2@{TEAM}", "team_name": TEAM},
            )
            scheduler._on_member_turn(msg_r1)
            scheduler._on_member_turn(msg_r2)

        assert alice.wait_task_queue.qsize() == 2

    async def test_room_can_requeue_after_consumed(self):
        """事件被消费后，同一房间应该可以再次入队。"""
        alice = _make_mock_member("alice")
        room = roomService.ChatRoom(
            team=GtTeam(id=1, name=TEAM),
            room=GtRoom(
                id=1,
                team_id=1,
                name="r1",
                type=roomService.RoomType.GROUP,
                initial_topic="",
                max_turns=0,
                member_read_index=None,
                updated_at=GtRoom._now(),
            ),
            members=[GtTeamMember(id=0, team_id=1, name="alice", agent_name="alice")],
        )
        teams_config = [_make_team_config()]
        await scheduler.startup(teams_config)

        with patch("service.schedulerService.memberService.get_team_member", return_value=alice):
            msg = Message(
                topic=MessageBusTopic.ROOM_MEMBER_TURN,
                payload={"member_name": "alice", "room_id": room.room_id, "room_name": "r1", "room_key": f"r1@{TEAM}", "team_name": TEAM},
            )
            # 第一次入队
            scheduler._on_member_turn(msg)
            assert alice.wait_task_queue.qsize() == 1

            # 消费掉
            alice.wait_task_queue.get_nowait()
            assert alice.wait_task_queue.qsize() == 0

            # 再次入队应该成功
            scheduler._on_member_turn(msg)
            assert alice.wait_task_queue.qsize() == 1

    async def test_refresh_team_config(self):
        """验证刷新团队配置。"""
        old_config = _make_team_config()
        await scheduler.startup([old_config])
        
        new_config = _make_team_config()
        new_config.max_function_calls = 10
        scheduler.refresh_team_config(TEAM, [new_config])
        
        assert scheduler._teams_config[0].max_function_calls == 10

    async def test_stop_team(self):
        """验证停止特定团队的调度。"""
        alice = _make_mock_member("alice")
        teams_config = [_make_team_config()]
        await scheduler.startup(teams_config)
        
        with patch("service.schedulerService.memberService.get_team_member", return_value=alice):
            scheduler.add_member(alice, 5)
            assert alice.key in scheduler._running
            
            scheduler.stop_team(TEAM)
            assert alice.key not in scheduler._running

    async def test_on_agent_turn_operator_ignored(self, caplog):
        """验证 OPERATOR 身份被忽略不进入调度。"""
        await scheduler.startup([])
        msg = Message(
            topic=MessageBusTopic.ROOM_MEMBER_TURN,
            payload={"member_name": "OPERATOR", "room_id": 1, "team_name": TEAM},
        )
        with caplog.at_level(logging.INFO):
            scheduler._on_member_turn(msg)
        assert "轮到人类操作者，系统进入等待状态" in caplog.text

    async def test_on_agent_turn_agent_not_found(self, caplog):
        """验证 Agent 找不到时的错误处理。"""
        await scheduler.startup([])
        msg = Message(
            topic=MessageBusTopic.ROOM_MEMBER_TURN,
            payload={"member_name": "non-existent", "room_id": 1, "team_name": TEAM},
        )
        with patch("service.schedulerService.memberService.get_team_member", side_effect=KeyError("not found")):
            with caplog.at_level(logging.ERROR):
                scheduler._on_member_turn(msg)
        assert "成员不存在" in caplog.text

    async def test_on_agent_turn_general_exception(self, caplog):
        """验证获取 Agent 发生通用异常时的错误处理。"""
        await scheduler.startup([])
        msg = Message(
            topic=MessageBusTopic.ROOM_MEMBER_TURN,
            payload={"member_name": "error-agent", "room_id": 1, "team_name": TEAM},
        )
        with patch("service.schedulerService.memberService.get_team_member", side_effect=RuntimeError("unexpected")):
            with caplog.at_level(logging.ERROR):
                scheduler._on_member_turn(msg)
        assert "获取成员失败" in caplog.text

    async def test_remove_agent_non_existent(self):
        """移除不存在的 agent 不应报错。"""
        scheduler.remove_member("non-existent@team")
        # No exception means success

    async def test_iter_team_rooms(self):
        """测试内部辅助函数 _iter_team_rooms。"""
        config = _make_team_config()
        rooms = scheduler._iter_team_rooms(config)
        assert len(rooms) == 1
        assert rooms[0].name == "r1"
