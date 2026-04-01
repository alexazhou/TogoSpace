import os
import sys
from unittest.mock import patch, call

import pytest

from service import roomService
import service.ormService as ormService
import service.persistenceService as persistenceService
from constants import RoomType, RoomState, MessageBusTopic, SpecialAgent
from dal.db import gtTeamManager, gtAgentManager
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtTeam import GtTeam
from ...base import ServiceTestCase

TEAM = "test_team"

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")



class TestRoomTurnLogic(ServiceTestCase):
    """覆盖房间轮转推进、finish_turn 与唤醒边界行为。"""

    @classmethod
    async def async_setup_class(cls):
        # 该文件所有用例都基于真实 ChatRoom 状态机进行断言。
        db_path = cls._get_test_db_path()
        await ormService.startup(db_path)
        await persistenceService.startup()
        await roomService.startup()

        # 预创建 team，_create_room 不再自动创建
        team = await gtTeamManager.save_team(GtTeam(name=TEAM))
        await gtAgentManager.batch_save_agents(
            team.id,
            [
                GtAgent(team_id=team.id, name="alice", role_template_id=0),
                GtAgent(team_id=team.id, name="bob", role_template_id=0),
                GtAgent(team_id=team.id, name="charlie", role_template_id=0),
                GtAgent(team_id=team.id, name="a", role_template_id=0),
                GtAgent(team_id=team.id, name="b", role_template_id=0),
            ],
        )

    @classmethod
    async def async_teardown_class(cls):
        roomService.shutdown()
        await persistenceService.shutdown()
        await ormService.shutdown()

    async def test_strict_turn_advancement(self):
        """
        测试点：严格顺序推进逻辑
        """
        room_name = "test_room"
        agents = ["alice", "bob", "charlie"]
        room_key = f"{room_name}@{TEAM}"
        await roomService.ensure_room_record(TEAM, room_name, agents, room_type=RoomType.GROUP, max_turns=10)
        room = roomService.get_room_by_key(room_key)
        assert await room.activate_scheduling()

        assert room.get_current_turn_agent() == "alice"
        assert room._turn_pos == 0

        with patch("service.messageBus.publish") as mock_publish:
            await room.add_message("alice", "hello")
            # 消息不再自动推进，手动结束回合
            room.finish_turn("alice")
            assert room.get_current_turn_agent() == "bob"
            assert room._turn_pos == 1
            mock_publish.assert_any_call(
                MessageBusTopic.ROOM_MEMBER_TURN,
                member_name="bob",
                room_id=room.room_id,
                room_name=room_name,
                room_key=room_key,
                team_name=TEAM,
            )

        with patch("service.messageBus.publish") as mock_publish:
            await room.add_message("charlie", "I am interrupting")
            # 插话不影响当前发言位，且即便插话也不会推进回合
            assert room.get_current_turn_agent() == "bob"
            assert room._turn_pos == 1
            topics = [call[0][0] for call in mock_publish.call_args_list]
            assert MessageBusTopic.ROOM_MSG_ADDED in topics
            assert MessageBusTopic.ROOM_MEMBER_TURN not in topics

        await room.add_message("bob", "responding to alice")
        room.finish_turn("bob")
        assert room.get_current_turn_agent() == "charlie"
        assert room._turn_pos == 2

    async def test_finish_turn_validation(self):
        """
        测试点：结束发言的身份校验
        """
        room_name = "test_skip"
        agents = ["alice", "bob"]
        await roomService.ensure_room_record(TEAM, room_name, agents, max_turns=10)
        room = roomService.get_room_by_key(f"{room_name}@{TEAM}")
        assert await room.activate_scheduling()

        room.finish_turn(sender="bob")
        assert room.get_current_turn_agent() == "alice"

        room.finish_turn(sender="alice")
        assert room.get_current_turn_agent() == "bob"

    async def test_idle_wakeup_logic(self):
        """
        测试点：最大轮次限制后的唤醒机制
        """
        room_name = "test_idle"
        agents = ["alice", "bob"]
        room_key = f"{room_name}@{TEAM}"
        await roomService.ensure_room_record(TEAM, room_name, agents, max_turns=1)
        room = roomService.get_room_by_key(room_key)
        assert await room.activate_scheduling()

        await room.add_message("alice", "hi")
        room.finish_turn("alice")
        await room.add_message("bob", "bye")
        room.finish_turn("bob")

        assert room.state == RoomState.IDLE
        assert room._turn_count == 1
        assert room.get_current_turn_agent() == "alice"

        with patch("service.messageBus.publish") as mock_publish:
            await room.add_message("bob", "wait, one more thing")

            assert room.state == RoomState.SCHEDULING
            assert room._turn_count == 0
            assert room.get_current_turn_agent() == "alice"

            mock_publish.assert_any_call(
                MessageBusTopic.ROOM_MEMBER_TURN,
                member_name="alice",
                room_id=room.room_id,
                room_name=room_name,
                room_key=room_key,
                team_name=TEAM,
            )

    async def test_full_loop_advancement(self):
        """
        测试点：完整轮次计数逻辑
        """
        room_name = "test_loop"
        agents = ["a", "b"]
        await roomService.ensure_room_record(TEAM, room_name, agents, max_turns=5)
        room = roomService.get_room_by_key(f"{room_name}@{TEAM}")
        assert await room.activate_scheduling()

        assert room._turn_count == 0

        await room.add_message("a", "1")
        room.finish_turn("a")
        assert room._turn_count == 0

        await room.add_message("b", "2")
        room.finish_turn("b")
        assert room._turn_count == 1
        assert room._turn_pos == 0
        assert room.get_current_turn_agent() == "a"

    # ------------------------------------------------------------------
    # 全员跳过时停止调度
    # ------------------------------------------------------------------

    async def test_all_skip_stops_scheduling(self):
        """
        测试点：同一轮内所有 AI Agent 均调用 finish_turn（未发言），本轮结束后房间立即进入 IDLE。
        """
        room_name = "skip_all"
        agents = ["alice", "bob"]
        await roomService.ensure_room_record(TEAM, room_name, agents, max_turns=10)
        room = roomService.get_room_by_key(f"{room_name}@{TEAM}")
        assert await room.activate_scheduling()
        assert room.state == RoomState.SCHEDULING

        with patch("service.messageBus.publish"):
            room.finish_turn(sender="alice")
            # 仅 alice 跳过，bob 尚未发言 -> 仍在调度
            assert room.state == RoomState.SCHEDULING

            room.finish_turn(sender="bob")
            # alice + bob 均跳过，本轮结束 -> IDLE
            assert room.state == RoomState.IDLE

    async def test_all_skip_no_further_turn_events(self):
        """
        测试点：全员跳过进入 IDLE 后，不再发布 ROOM_MEMBER_TURN 事件。
        """
        room_name = "skip_no_event"
        agents = ["alice", "bob"]
        await roomService.ensure_room_record(TEAM, room_name, agents, max_turns=10)
        room = roomService.get_room_by_key(f"{room_name}@{TEAM}")
        assert await room.activate_scheduling()

        with patch("service.messageBus.publish") as mock_publish:
            room.finish_turn(sender="alice")
            room.finish_turn(sender="bob")

            turn_calls = [
                c for c in mock_publish.call_args_list
                if c[0][0] == MessageBusTopic.ROOM_MEMBER_TURN
            ]
            # start_scheduling 时已发布 alice 的初始事件（在 mock 外），
            # mock 内：finish alice -> bob 事件，finish bob -> 全员跳过，不再发布
            agent_names_notified = [c[1]["member_name"] for c in turn_calls]
            assert agent_names_notified == ["bob"]

    async def test_all_skip_wakeup_based_on_state_not_turn_count(self):
        """
        测试点：全员跳过进入 IDLE 时，_turn_count 不会被人为抬高到 _max_turns；
        唤醒逻辑只依赖房间状态（IDLE），与 _turn_count 无关。
        """
        room_name = "skip_idx"
        agents = ["alice", "bob"]
        room_key = f"{room_name}@{TEAM}"
        await roomService.ensure_room_record(TEAM, room_name, agents, max_turns=10)
        room = roomService.get_room_by_key(room_key)
        assert await room.activate_scheduling()

        with patch("service.messageBus.publish"):
            room.finish_turn(sender="alice")
            room.finish_turn(sender="bob")

        assert room.state == RoomState.IDLE
        # _turn_count 应为自然推进值（1），不被强制拉到 _max_turns
        assert room._turn_count == 1
        assert room._turn_count < room._max_turns

        # 即便 _turn_count 远小于 _max_turns，发消息依然能唤醒房间
        with patch("service.messageBus.publish"):
            await room.add_message("alice", "back")

        assert room.state == RoomState.SCHEDULING
        assert room._turn_count == 0

    async def test_all_skip_wakeup_by_operator(self):
        """
        测试点：全员跳过进入 IDLE 后，Operator 发一条消息能重新唤醒调度。
        """
        room_name = "skip_wakeup"
        agents = [SpecialAgent.OPERATOR, "alice", "bob"]
        room_key = f"{room_name}@{TEAM}"
        await roomService.ensure_room_record(TEAM, room_name, agents, max_turns=10)
        room = roomService.get_room_by_key(room_key)
        assert await room.activate_scheduling()

        with patch("service.messageBus.publish"):
            room.finish_turn(sender="alice")
            room.finish_turn(sender="bob")

        assert room.state == RoomState.IDLE

        with patch("service.messageBus.publish") as mock_publish:
            await room.add_message(SpecialAgent.OPERATOR.name, "wake up")
            assert room.state == RoomState.SCHEDULING
            assert room._turn_count == 0
            assert room.get_current_turn_agent() == "alice"

            turn_calls = [
                c for c in mock_publish.call_args_list
                if c[0][0] == MessageBusTopic.ROOM_MEMBER_TURN
            ]
            assert len(turn_calls) >= 1
            assert turn_calls[-1][1]["member_name"] == "alice"

    async def test_partial_skip_does_not_stop(self):
        """
        测试点：只有部分 Agent 跳过时，调度不停止，房间继续推进。
        """
        room_name = "skip_partial"
        agents = ["alice", "bob", "charlie"]
        await roomService.ensure_room_record(TEAM, room_name, agents, max_turns=10)
        room = roomService.get_room_by_key(f"{room_name}@{TEAM}")
        assert await room.activate_scheduling()

        with patch("service.messageBus.publish"):
            room.finish_turn(sender="alice")   # alice 跳过
            await room.add_message("bob", "hi")    # bob 正常发言
            room.finish_turn("bob")
            room.finish_turn(sender="charlie") # charlie 跳过

        # 本轮 bob 发了言，不是全员跳过 -> 轮次正常推进，房间仍在调度
        assert room.state == RoomState.SCHEDULING
        assert room._turn_count == 1

    async def test_operator_auto_skip_keeps_all_skip_stop_logic(self):
        """
        测试点：多人群里 Operator 自动 skip 后，仍能正确复用“AI 全员 skip 即停止”的逻辑。
        """
        room_name = "skip_op"
        agents = ["alice", SpecialAgent.OPERATOR, "bob"]
        await roomService.ensure_room_record(TEAM, room_name, agents, max_turns=10)
        room = roomService.get_room_by_key(f"{room_name}@{TEAM}")
        assert await room.activate_scheduling()

        with patch("service.messageBus.publish") as mock_publish:
            room.finish_turn(sender="alice")
            turn_calls = [
                c for c in mock_publish.call_args_list
                if c[0][0] == MessageBusTopic.ROOM_MEMBER_TURN
            ]
            assert [c[1]["member_name"] for c in turn_calls] == ["bob"]

        with patch("service.messageBus.publish"):
            room.finish_turn(sender="bob")

        assert room.state == RoomState.IDLE

    async def test_multi_member_group_auto_skips_operator_turn(self):
        """
        测试点：多人群里遇到 Operator 回合时，不等待人类输入，直接自动跳到下一位 AI。
        """
        room_name = "operator_auto_skip"
        agents = ["alice", SpecialAgent.OPERATOR, "bob"]
        await roomService.ensure_room_record(TEAM, room_name, agents, max_turns=10)
        room = roomService.get_room_by_key(f"{room_name}@{TEAM}")
        assert await room.activate_scheduling()
        assert room.get_current_turn_agent() == "alice"

        with patch("service.messageBus.publish") as mock_publish:
            await room.add_message("alice", "hello from alice")
            ok = room.finish_turn("alice")
            assert ok is True

        turn_calls = [
            c for c in mock_publish.call_args_list
            if c[0][0] == MessageBusTopic.ROOM_MEMBER_TURN
        ]
        assert [c[1]["member_name"] for c in turn_calls] == ["bob"]
        assert room.get_current_turn_agent() == "bob"

    async def test_two_member_group_still_waits_for_operator_turn(self):
        """
        测试点：两人群里遇到 Operator 时，仍保持原有等待逻辑，不自动 skip。
        """
        room_name = "operator_wait_group"
        agents = ["alice", SpecialAgent.OPERATOR]
        await roomService.ensure_room_record(TEAM, room_name, agents, room_type=RoomType.GROUP, max_turns=10)
        room = roomService.get_room_by_key(f"{room_name}@{TEAM}")
        assert await room.activate_scheduling()
        assert room.get_current_turn_agent() == "alice"

        with patch("service.messageBus.publish") as mock_publish:
            await room.add_message("alice", "hello from alice")
            ok = room.finish_turn("alice")
            assert ok is True

        turn_calls = [
            c for c in mock_publish.call_args_list
            if c[0][0] == MessageBusTopic.ROOM_MEMBER_TURN
        ]
        assert [c[1]["member_name"] for c in turn_calls] == [SpecialAgent.OPERATOR.name]
        assert room.get_current_turn_agent() == SpecialAgent.OPERATOR.name

    async def test_operator_alias_matches_on_turn_checks(self):
        """
        测试点：当前发言位是配置中的 "Operator" 时，运行态传入 "OPERATOR"
        也应识别为同一 SpecialAgent，不应被判定为插话或非法结束轮次。
        """
        room_name = "operator_alias"
        agents = ["Operator", "alice"]
        await roomService.ensure_room_record(TEAM, room_name, agents, max_turns=10)
        room = roomService.get_room_by_key(f"{room_name}@{TEAM}")
        assert await room.activate_scheduling()
        assert room.get_current_turn_agent() == SpecialAgent.OPERATOR.name

        with patch("service.messageBus.publish"):
            await room.add_message(SpecialAgent.OPERATOR.name, "hello from operator")
            ok = room.finish_turn(SpecialAgent.OPERATOR.name)
            assert ok is True

        assert room.get_current_turn_agent() == "alice"

    async def test_skip_set_resets_each_round(self):
        """
        测试点：每轮的跳过记录互不干扰——第一轮全员跳过停止后唤醒，
        第二轮部分跳过不应再次停止。
        """
        room_name = "skip_reset"
        agents = ["alice", "bob"]
        await roomService.ensure_room_record(TEAM, room_name, agents, max_turns=10)
        room = roomService.get_room_by_key(f"{room_name}@{TEAM}")
        assert await room.activate_scheduling()

        with patch("service.messageBus.publish"):
            # 第一轮：全员跳过 -> IDLE
            room.finish_turn(sender="alice")
            room.finish_turn(sender="bob")
        assert room.state == RoomState.IDLE

        with patch("service.messageBus.publish"):
            # alice 发消息唤醒房间，同时推进到 bob
            await room.add_message("alice", "I'm back")
            room.finish_turn("alice")
        assert room.state == RoomState.SCHEDULING

        with patch("service.messageBus.publish"):
            # 第二轮：只有 bob 跳过，alice 已发言
            room.finish_turn(sender="bob")

        # 第二轮不是全员跳过（alice 正常发言），房间应继续调度
        assert room.state == RoomState.SCHEDULING

    async def test_sliding_window_skip_stop(self):
        """
        测试点：滑动窗口跳过判定。
        当所有 AI Agent 自上次发言以来都至少跳过一次，立即停止调度（无需等到本轮结束）。
        场景：Alice 发言 -> Alice 结束 -> Bob 跳过 -> Charlie 跳过 -> (下一轮) Alice 跳过 -> 立即停止。
        """
        room_name = "test_sliding"
        agents = ["alice", "bob", "charlie"]
        await roomService.ensure_room_record(TEAM, room_name, agents, max_turns=10)
        room = roomService.get_room_by_key(f"{room_name}@{TEAM}")
        assert await room.activate_scheduling()

        with patch("service.messageBus.publish"):
            # 1. Alice 发言
            await room.add_message("alice", "hello")
            room.finish_turn("alice") # pos -> 1 (bob)

            # 2. Bob 跳过
            room.finish_turn("bob") # pos -> 2 (charlie), skipped={bob}
            assert room.state == RoomState.SCHEDULING

            # 3. Charlie 跳过
            room.finish_turn("charlie") # pos -> 0 (alice), index -> 1, skipped={bob, charlie}
            assert room.state == RoomState.SCHEDULING

            # 4. Alice 跳过
            # 此时 AI 成员全员自上次消息以来都已跳过，应立即停止，不再分发给 Bob
            room.finish_turn("alice") # pos -> 1 (bob), index -> 1, skipped={bob, charlie, alice}
            
        assert room.state == RoomState.IDLE
        assert room.get_current_turn_agent() == "bob"
        assert room._turn_count == 1
