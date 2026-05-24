import os
import sys
from unittest.mock import patch, call

import pytest

from service import roomService, agentService
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
        await agentService.startup()  # 确保 SpecialAgent 记录存在
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
        cls.team_id = team.id

    @classmethod
    async def async_teardown_class(cls):
        roomService.shutdown()
        await agentService.shutdown()
        await persistenceService.shutdown()
        await ormService.shutdown()

    async def _get_agent_id(self, name: str) -> int | None:
        gt_agent = await gtAgentManager.get_agent(self.team_id, name)
        return gt_agent.id if gt_agent else None

    async def test_strict_turn_advancement(self):
        """
        测试点：严格顺序推进逻辑
        """
        room_name = "test_room"
        agents = ["alice", "bob", "charlie"]
        room_key = f"{room_name}@{TEAM}"
        await self.create_room(TEAM, room_name, agents, room_type=RoomType.GROUP, max_rounds=10)
        room = roomService.get_room_by_key(room_key)
        assert await room.activate_scheduling()

        assert gtAgentManager.get_agent_name(room.get_current_turn_agent_id()) == "alice"
        assert room._current_speaker_index == 0

        alice_id = await self._get_agent_id("alice")
        bob_id = await self._get_agent_id("bob")
        charlie_id = await self._get_agent_id("charlie")

        with patch("service.messageBus.publish") as mock_publish:
            await room.add_message(alice_id, "hello")
            # 消息不再自动推进，手动结束回合
            await room.handle_finish_request(alice_id)
            assert gtAgentManager.get_agent_name(room.get_current_turn_agent_id()) == "bob"
            assert room._current_speaker_index == 1
            mock_publish.assert_any_call(
                MessageBusTopic.ROOM_STATUS_CHANGED,
                gt_room=room.gt_room,
                state=RoomState.SCHEDULING,
                current_turn_agent_id=bob_id,
                need_scheduling=True,
            )

        with patch("service.messageBus.publish") as mock_publish:
            await room.add_message(charlie_id, "I am interrupting")
            # 插话不影响当前发言位，且即便插话也不会推进回合
            assert gtAgentManager.get_agent_name(room.get_current_turn_agent_id()) == "bob"
            assert room._current_speaker_index == 1
            topics = [call[0][0] for call in mock_publish.call_args_list]
            assert MessageBusTopic.ROOM_MSG_ADDED in topics
            scheduling_calls = [
                c for c in mock_publish.call_args_list
                if c[0][0] == MessageBusTopic.ROOM_STATUS_CHANGED
            ]
            assert scheduling_calls == []

        await room.add_message(bob_id, "responding to alice")
        await room.handle_finish_request(bob_id)
        assert gtAgentManager.get_agent_name(room.get_current_turn_agent_id()) == "charlie"
        assert room._current_speaker_index == 2

    async def test_finish_turn_validation(self):
        """
        测试点：结束发言的身份校验
        """
        room_name = "test_skip"
        agents = ["alice", "bob"]
        await self.create_room(TEAM, room_name, agents, max_rounds=10)
        room = roomService.get_room_by_key(f"{room_name}@{TEAM}")
        assert await room.activate_scheduling()

        alice_id = await self._get_agent_id("alice")
        bob_id = await self._get_agent_id("bob")

        await room.handle_finish_request(bob_id)
        assert gtAgentManager.get_agent_name(room.get_current_turn_agent_id()) == "alice"

        await room.handle_finish_request(alice_id)
        assert gtAgentManager.get_agent_name(room.get_current_turn_agent_id()) == "bob"

    async def test_idle_wakeup_logic(self):
        """
        测试点：最大轮次限制后的唤醒机制（由 OPERATOR 发消息触发）
        """
        room_name = "test_idle"
        agents = ["alice", "bob", "OPERATOR"]
        room_key = f"{room_name}@{TEAM}"
        await self.create_room(TEAM, room_name, agents, max_rounds=1)
        room = roomService.get_room_by_key(room_key)
        assert await room.activate_scheduling()

        alice_id = await self._get_agent_id("alice")
        bob_id = await self._get_agent_id("bob")

        await room.add_message(alice_id, "hi")
        await room.handle_finish_request(alice_id)
        await room.add_message(bob_id, "bye")
        await room.handle_finish_request(bob_id)

        assert room.state == RoomState.IDLE
        assert room._round_count == 1  # 末位绕回触发轮次计数自增
        assert gtAgentManager.get_agent_name(room.get_current_turn_agent_id()) == "alice"  # 绕回首位

        with patch("service.messageBus.publish") as mock_publish:
            await room.add_message(room.OPERATOR_MEMBER_ID, "wait, one more thing")

            assert room.state == RoomState.SCHEDULING
            assert room._round_count == 0
            assert gtAgentManager.get_agent_name(room.get_current_turn_agent_id()) == "alice"

            mock_publish.assert_any_call(
                MessageBusTopic.ROOM_STATUS_CHANGED,
                gt_room=room.gt_room,
                state=RoomState.SCHEDULING,
                current_turn_agent_id=alice_id,
                need_scheduling=True,
            )

    async def test_idle_wakeup_by_non_current_agent_message(self):
        """
        测试点：IDLE 群聊中，非当前发言位的普通 Agent 发消息时，应唤醒房间并调度当前发言位。
        """
        room_name = "idle_agent_wakeup"
        agents = ["alice", "bob", "charlie"]
        room_key = f"{room_name}@{TEAM}"
        await self.create_room(TEAM, room_name, agents, max_rounds=10)
        room = roomService.get_room_by_key(room_key)
        assert await room.activate_scheduling()

        alice_id = await self._get_agent_id("alice")
        bob_id = await self._get_agent_id("bob")
        charlie_id = await self._get_agent_id("charlie")

        with patch("service.messageBus.publish"):
            await room.handle_finish_request(alice_id)
            await room.handle_finish_request(bob_id)
            await room.handle_finish_request(charlie_id)

        assert room.state == RoomState.IDLE

        # alice(index 0) 发消息唤醒房间，应调度 alice 的下一位 bob(index 1)
        with patch("service.messageBus.publish") as mock_publish:
            await room.add_message(alice_id, "hello from another room")

            assert room.state == RoomState.SCHEDULING
            assert room._round_count == 0
            # alice(唤醒者)被 _should_skip 跳过并加入 skip set
            assert room._round_skipped_set == {alice_id}
            assert room.get_current_turn_agent_id() == bob_id

            turn_calls = [
                c for c in mock_publish.call_args_list
                if c[0][0] == MessageBusTopic.ROOM_STATUS_CHANGED
                and c[1].get("need_scheduling")
            ]
            assert len(turn_calls) == 1
            assert turn_calls[0][1]["current_turn_agent_id"] == bob_id

    async def test_full_loop_advancement(self):
        """
        测试点：完整轮次计数逻辑
        """
        room_name = "test_loop"
        agents = ["a", "b"]
        await self.create_room(TEAM, room_name, agents, max_rounds=5)
        room = roomService.get_room_by_key(f"{room_name}@{TEAM}")
        assert await room.activate_scheduling()

        a_id = await self._get_agent_id("a")
        b_id = await self._get_agent_id("b")

        assert room._round_count == 0

        await room.add_message(a_id, "1")
        await room.handle_finish_request(a_id)
        assert room._round_count == 0

        await room.add_message(b_id, "2")
        await room.handle_finish_request(b_id)
        assert room._round_count == 1
        assert room._current_speaker_index == 0
        assert gtAgentManager.get_agent_name(room.get_current_turn_agent_id()) == "a"

    # ------------------------------------------------------------------
    # 全员跳过时停止调度
    # ------------------------------------------------------------------

    async def test_all_skip_stops_scheduling(self):
        """
        测试点：同一轮内所有 AI Agent 均调用 finish_turn（未发言），本轮结束后房间立即进入 IDLE。
        """
        room_name = "skip_all"
        agents = ["alice", "bob"]
        await self.create_room(TEAM, room_name, agents, max_rounds=10)
        room = roomService.get_room_by_key(f"{room_name}@{TEAM}")
        assert await room.activate_scheduling()
        assert room.state == RoomState.SCHEDULING

        alice_id = await self._get_agent_id("alice")
        bob_id = await self._get_agent_id("bob")
        with patch("service.messageBus.publish"):
            await room.handle_finish_request(alice_id)
            # 仅 alice 跳过，bob 尚未发言 -> 仍在调度
            assert room.state == RoomState.SCHEDULING

            await room.handle_finish_request(bob_id)
            # alice + bob 均跳过，本轮结束 -> IDLE
            assert room.state == RoomState.IDLE

    async def test_all_skip_no_further_turn_events(self):
        """
        测试点：全员跳过进入 IDLE 后，不再发布 ROOM_AGENT_TURN 事件。
        """
        room_name = "skip_no_event"
        agents = ["alice", "bob"]
        await self.create_room(TEAM, room_name, agents, max_rounds=10)
        room = roomService.get_room_by_key(f"{room_name}@{TEAM}")
        assert await room.activate_scheduling()

        alice_id = await self._get_agent_id("alice")
        bob_id = await self._get_agent_id("bob")
        with patch("service.messageBus.publish") as mock_publish:
            await room.handle_finish_request(alice_id)
            await room.handle_finish_request(bob_id)

            turn_calls = [
                c for c in mock_publish.call_args_list
                if c[0][0] == MessageBusTopic.ROOM_STATUS_CHANGED
                and c[1].get("need_scheduling")
            ]
            # start_scheduling 时已发布 alice 的初始事件（在 mock 外），
            # mock 内：finish alice -> bob 事件，finish bob -> 全员跳过，不再发布
            agent_ids_notified = [c[1]["current_turn_agent_id"] for c in turn_calls]
            assert agent_ids_notified == [bob_id]

    async def test_all_skip_wakeup_based_on_state_not_round_count(self):
        """
        测试点：全员跳过进入 IDLE 时，_round_count 不会被人为抬高到 _max_rounds；
        唤醒逻辑只依赖房间状态（IDLE），与 _round_count 无关。由 OPERATOR 发消息触发唤醒。
        """
        room_name = "skip_idx"
        agents = ["alice", "bob", "OPERATOR"]
        room_key = f"{room_name}@{TEAM}"
        await self.create_room(TEAM, room_name, agents, max_rounds=10)
        room = roomService.get_room_by_key(room_key)
        assert await room.activate_scheduling()

        with patch("service.messageBus.publish"):
            await room.handle_finish_request(await self._get_agent_id("alice"))
            await room.handle_finish_request(await self._get_agent_id("bob"))

        assert room.state == RoomState.IDLE
        # 全员跳过，未发生回到首位，_round_count 停留在 0
        assert room._round_count == 0
        assert room._round_count < room._max_rounds

        # 即便 _round_count 远小于 _max_rounds，OPERATOR 发消息依然能唤醒房间
        with patch("service.messageBus.publish"):
            await room.add_message(room.OPERATOR_MEMBER_ID, "back")

        assert room.state == RoomState.SCHEDULING
        assert room._round_count == 0

    async def test_all_skip_wakeup_by_operator(self):
        """
        测试点：全员跳过进入 IDLE 后，Operator 发一条消息能重新唤醒调度。
        """
        room_name = "skip_wakeup"
        agents = ["OPERATOR", "alice", "bob"]
        room_key = f"{room_name}@{TEAM}"
        await self.create_room(TEAM, room_name, agents, max_rounds=10)
        room = roomService.get_room_by_key(room_key)
        assert await room.activate_scheduling()

        alice_id = await self._get_agent_id("alice")
        bob_id = await self._get_agent_id("bob")
        with patch("service.messageBus.publish"):
            await room.handle_finish_request(alice_id)
            await room.handle_finish_request(bob_id)

        assert room.state == RoomState.IDLE

        with patch("service.messageBus.publish") as mock_publish:
            await room.add_message(room.OPERATOR_MEMBER_ID, "wake up")
            assert room.state == RoomState.SCHEDULING
            assert room._round_count == 0
            # 从 IDLE 唤醒时 _current_speaker_index 为 None，从 index 0 开始；OPERATOR 被跳过，alice 排第一
            assert gtAgentManager.get_agent_name(room.get_current_turn_agent_id()) == "alice"

            turn_calls = [
                c for c in mock_publish.call_args_list
                if c[0][0] == MessageBusTopic.ROOM_STATUS_CHANGED
                and c[1].get("need_scheduling")
            ]
            assert len(turn_calls) >= 1
            assert turn_calls[-1][1]["current_turn_agent_id"] == await self._get_agent_id("alice")

    async def test_manual_stop_wakeup_by_operator(self):
        """
        测试点：人工停止当前 turn 后，房间应回到 IDLE，后续 Operator 消息能重新唤醒原发言人。
        """
        room_name = "manual_stop_wakeup"
        agents = ["alice", "OPERATOR"]
        room_key = f"{room_name}@{TEAM}"
        await self.create_room(TEAM, room_name, agents, max_rounds=10)
        room = roomService.get_room_by_key(room_key)
        assert await room.activate_scheduling()

        assert room.state == RoomState.SCHEDULING
        assert gtAgentManager.get_agent_name(room.get_current_turn_agent_id()) == "alice"

        with patch("service.messageBus.publish") as mock_publish:
            room.cancel_current_turn()

            assert room.state == RoomState.IDLE
            idle_calls = [
                c for c in mock_publish.call_args_list
                if c[0][0] == MessageBusTopic.ROOM_STATUS_CHANGED
                and c[1].get("need_scheduling") is False
            ]
            assert len(idle_calls) >= 1

        with patch("service.messageBus.publish") as mock_publish:
            await room.add_message(room.OPERATOR_MEMBER_ID, "continue")

            assert room.state == RoomState.SCHEDULING
            assert gtAgentManager.get_agent_name(room.get_current_turn_agent_id()) == "alice"

            turn_calls = [
                c for c in mock_publish.call_args_list
                if c[0][0] == MessageBusTopic.ROOM_STATUS_CHANGED
                and c[1].get("need_scheduling")
            ]
            assert len(turn_calls) >= 1
            assert turn_calls[-1][1]["current_turn_agent_id"] == await self._get_agent_id("alice")

    async def test_partial_skip_does_not_stop(self):
        """
        测试点：只有部分 Agent 跳过时，调度不停止，房间继续推进。
        """
        room_name = "skip_partial"
        agents = ["alice", "bob", "charlie"]
        await self.create_room(TEAM, room_name, agents, max_rounds=10)
        room = roomService.get_room_by_key(f"{room_name}@{TEAM}")
        assert await room.activate_scheduling()

        with patch("service.messageBus.publish"):
            bob_id = await self._get_agent_id("bob")
            await room.handle_finish_request(await self._get_agent_id("alice"))   # alice 跳过
            await room.add_message(bob_id, "hi")    # bob 正常发言
            await room.handle_finish_request(bob_id)
            await room.handle_finish_request(await self._get_agent_id("charlie")) # charlie 跳过

        # 本轮 bob 发了言，不是全员跳过 -> 轮次正常推进，房间仍在调度
        assert room.state == RoomState.SCHEDULING
        assert room._round_count == 1

    async def test_operator_auto_skip_keeps_all_skip_stop_logic(self):
        """
        测试点：多人群里 Operator 自动 skip 后，仍能正确复用"AI 全员 skip 即停止"的逻辑。
        """
        room_name = "skip_op"
        agents = ["alice", SpecialAgent.OPERATOR, "bob"]
        await self.create_room(TEAM, room_name, agents, max_rounds=10)
        room = roomService.get_room_by_key(f"{room_name}@{TEAM}")
        assert await room.activate_scheduling()

        alice_id = await self._get_agent_id("alice")
        bob_id = await self._get_agent_id("bob")
        with patch("service.messageBus.publish") as mock_publish:
            await room.handle_finish_request(alice_id)
            turn_calls = [
                c for c in mock_publish.call_args_list
                if c[0][0] == MessageBusTopic.ROOM_STATUS_CHANGED
                and c[1].get("need_scheduling")
            ]
            assert [c[1]["current_turn_agent_id"] for c in turn_calls] == [bob_id]

        with patch("service.messageBus.publish"):
            await room.handle_finish_request(bob_id)

        assert room.state == RoomState.IDLE

    async def test_multi_agent_group_auto_skips_operator_turn(self):
        """
        测试点：多人群里遇到 Operator 回合时，不等待人类输入，直接自动跳到下一位 AI。
        """
        room_name = "operator_auto_skip"
        agents = ["alice", SpecialAgent.OPERATOR, "bob"]
        await self.create_room(TEAM, room_name, agents, max_rounds=10)
        room = roomService.get_room_by_key(f"{room_name}@{TEAM}")
        assert await room.activate_scheduling()
        assert gtAgentManager.get_agent_name(room.get_current_turn_agent_id()) == "alice"

        alice_id = await self._get_agent_id("alice")
        with patch("service.messageBus.publish") as mock_publish:
            await room.add_message(alice_id, "hello from alice")
            ok = await room.handle_finish_request(alice_id)
            assert ok is True

        turn_calls = [
            c for c in mock_publish.call_args_list
            if c[0][0] == MessageBusTopic.ROOM_STATUS_CHANGED
            and c[1].get("state") == RoomState.SCHEDULING
            and c[1].get("current_turn_agent_id") is not None
        ]
        assert [c[1]["current_turn_agent_id"] for c in turn_calls] == [await self._get_agent_id("bob")]
        assert gtAgentManager.get_agent_name(room.get_current_turn_agent_id()) == "bob"

    async def test_two_agent_group_still_waits_for_operator_turn(self):
        """
        测试点：[alice, OPERATOR] 群聊中，alice 完成一轮发言后进入 IDLE。
        OPERATOR 始终被跳过，advance 时 alice 因 _last_speaker_id 触发全员跳过，房间自动 IDLE。
        """
        room_name = "operator_wait_group"
        agents = ["alice", "OPERATOR"]
        await self.create_room(TEAM, room_name, agents, room_type=RoomType.GROUP, max_rounds=10)
        room = roomService.get_room_by_key(f"{room_name}@{TEAM}")
        assert await room.activate_scheduling()
        assert gtAgentManager.get_agent_name(room.get_current_turn_agent_id()) == "alice"

        alice_id = await self._get_agent_id("alice")
        with patch("service.messageBus.publish"):
            await room.add_message(alice_id, "hello from alice")
            ok = await room.handle_finish_request(alice_id)
            assert ok is True

        # alice 完成发言 → OPERATOR 跳过 → alice 触发 _last_speaker_id skip → 全员跳过 → IDLE
        assert room.state == RoomState.IDLE

    async def test_operator_alias_matches_on_turn_checks(self):
        """
        测试点：agents 列表包含 "OPERATOR" 时，OPERATOR 始终被跳过，alice 直接被调度。
        配置中的 "OPERATOR" 字符串与运行态的 OPERATOR_MEMBER_ID 等价。
        """
        room_name = "operator_alias"
        agents = ["OPERATOR", "alice"]
        await self.create_room(TEAM, room_name, agents, max_rounds=10)
        room = roomService.get_room_by_key(f"{room_name}@{TEAM}")
        assert await room.activate_scheduling()
        # OPERATOR 被跳过，alice 直接被调度
        assert gtAgentManager.get_agent_name(room.get_current_turn_agent_id()) == "alice"

        with patch("service.messageBus.publish"):
            # OPERATOR 发消息不影响当前发言位（alice 仍是当前发言人）
            await room.add_message(room.OPERATOR_MEMBER_ID, "hello from operator")

        assert gtAgentManager.get_agent_name(room.get_current_turn_agent_id()) == "alice"

    async def test_private_room_idle_when_operator_is_next(self):
        """
        测试点：PRIVATE 房间 AI 完成发言后，OPERATOR 被跳过，
        同一 AI 将再次被轮到，触发私聊停止条件 2（同一 Agent 连续两轮），
        房间切换到 IDLE 状态，前端可正确显示"空闲"。
        """
        room_name = "priv_idle"
        agents = ["alice", "OPERATOR"]
        await self.create_room(TEAM, room_name, agents, room_type=RoomType.PRIVATE, max_rounds=10)
        room = roomService.get_room_by_key(f"{room_name}@{TEAM}")
        await room.activate_scheduling()
        assert gtAgentManager.get_agent_name(room.get_current_turn_agent_id()) == "alice"

        alice_id = await self._get_agent_id("alice")
        with patch("service.messageBus.publish") as mock_publish:
            await room.add_message(alice_id, "hello from alice")
            ok = await room.handle_finish_request(alice_id)
            assert ok is True

        assert room.state == RoomState.IDLE
        assert gtAgentManager.get_agent_name(room.get_current_turn_agent_id()) == "alice"
        idle_calls = [
            c for c in mock_publish.call_args_list
            if c[0][0] == MessageBusTopic.ROOM_STATUS_CHANGED
            and c[1].get("state") == RoomState.IDLE
        ]
        assert len(idle_calls) == 1

    async def test_skip_set_resets_each_round(self):
        """
        测试点：每轮的跳过记录互不干扰——第一轮全员跳过停止后，
        OPERATOR 唤醒后 skip_set 已重置，第二轮部分跳过不应再次停止。
        """
        room_name = "skip_reset"
        agents = ["alice", "bob", "OPERATOR"]
        await self.create_room(TEAM, room_name, agents, max_rounds=10)
        room = roomService.get_room_by_key(f"{room_name}@{TEAM}")
        assert await room.activate_scheduling()

        alice_id = await self._get_agent_id("alice")
        bob_id = await self._get_agent_id("bob")
        with patch("service.messageBus.publish"):
            # 第一轮：全员跳过 -> IDLE
            await room.handle_finish_request(alice_id)
            await room.handle_finish_request(bob_id)
        assert room.state == RoomState.IDLE

        with patch("service.messageBus.publish"):
            # OPERATOR 发消息唤醒房间，skip_set 重置，bob 继续（保留 index）
            await room.add_message(room.OPERATOR_MEMBER_ID, "I'm back")
        assert room.state == RoomState.SCHEDULING

        with patch("service.messageBus.publish"):
            # 第二轮：只有 bob 跳过，alice 未跳过（skip_set 已重置）
            await room.handle_finish_request(bob_id)

        # 第二轮不是全员跳过，房间应继续调度
        assert room.state == RoomState.SCHEDULING

    async def test_idle_wakeup_by_current_speaker_index_agent_after_max_rounds(self):
        """
        回归测试：群聊完成最大轮次进入 IDLE 后，任意 Agent 发消息都能唤醒房间，
        且从发送者的下一位开始调度。

        Bug 路径：on_message 先检查 sender_id == current_id，若 IDLE 时恰好指针停在发送者，
        则只设置 current_turn_has_content=True 并返回 None，房间永远不离开 IDLE 状态。

        复现条件：[alice, bob] max_rounds=1 → 完成后 IDLE → alice 发消息 → 应唤醒并调度 bob
        """
        room_name = "idle_wakeup_current_max_rounds"
        agents = ["alice", "bob"]
        room_key = f"{room_name}@{TEAM}"
        await self.create_room(TEAM, room_name, agents, max_rounds=1)
        room = roomService.get_room_by_key(room_key)
        assert await room.activate_scheduling()

        alice_id = await self._get_agent_id("alice")
        bob_id = await self._get_agent_id("bob")

        # 完成 max_rounds=1：alice 发言 -> bob 发言 -> 绕回首位 round_count=1 >= 1 -> IDLE
        with patch("service.messageBus.publish"):
            await room.add_message(alice_id, "hello")
            await room.handle_finish_request(alice_id)
            await room.add_message(bob_id, "world")
            await room.handle_finish_request(bob_id)

        assert room.state == RoomState.IDLE

        # alice 发一条跨房间消息到此 IDLE 群聊，应唤醒并调度 alice 的下一位 bob
        with patch("service.messageBus.publish") as mock_publish:
            await room.add_message(alice_id, "a message from alice in another room")

            assert room.state == RoomState.SCHEDULING
            assert room._round_count == 0
            assert room._round_skipped_set == {alice_id}

            turn_calls = [
                c for c in mock_publish.call_args_list
                if c[0][0] == MessageBusTopic.ROOM_STATUS_CHANGED
                and c[1].get("need_scheduling")
            ]
            assert len(turn_calls) == 1
            assert turn_calls[0][1]["current_turn_agent_id"] == bob_id

    async def test_idle_wakeup_by_current_speaker_index_agent_after_all_skip(self):
        """
        回归测试：全员跳过进入 IDLE 后、任意 Agent 发送跨房间消息时，房间应被唤醒。

        进入 IDLE 时 _current_speaker_index 重置为 None；唤醒后从 index 0 重新开始调度。
        此测试验证 bob（非 index 0）发消息时也能正确唤醒房间。
        """
        room_name = "idle_wakeup_current_all_skip"
        agents = ["alice", "bob"]
        room_key = f"{room_name}@{TEAM}"
        await self.create_room(TEAM, room_name, agents, max_rounds=10)
        room = roomService.get_room_by_key(room_key)
        assert await room.activate_scheduling()

        alice_id = await self._get_agent_id("alice")
        bob_id = await self._get_agent_id("bob")

        with patch("service.messageBus.publish"):
            await room.handle_finish_request(alice_id)  # alice skip
            await room.handle_finish_request(bob_id)    # bob skip -> all skipped -> IDLE

        assert room.state == RoomState.IDLE
        # 进入 IDLE 后 _current_speaker_index 为 None，get_current_turn_agent_id() 返回 index 0 (alice)
        assert room.get_current_turn_agent_id() == alice_id

        # bob 发一条跨房间消息到此 IDLE 群聊
        with patch("service.messageBus.publish") as mock_publish:
            await room.add_message(bob_id, "a message from bob in another room")

            # 房间应从 IDLE 唤醒进入 SCHEDULING，从 index 0 (alice) 开始
            assert room.state == RoomState.SCHEDULING
            assert room._round_count == 0
            assert room._round_skipped_set == set()

            turn_calls = [
                c for c in mock_publish.call_args_list
                if c[0][0] == MessageBusTopic.ROOM_STATUS_CHANGED
                and c[1].get("need_scheduling")
            ]
            assert len(turn_calls) == 1
            assert turn_calls[0][1]["current_turn_agent_id"] == alice_id

    async def test_idle_wakeup_by_sender_schedules_next_agent(self):
        """
        测试点：IDLE 房间中，A 主动发言后下一个应直接调度到 B，不需要 A 再 finish。

        发送者的消息视为其本轮发言，on_message 唤醒时从发送者的下一位开始调度。
        场景：[alice(0), bob(1), charlie(2)]，跑完 1 轮 IDLE → alice 发消息 → bob 被调度。
        """
        room_name = "idle_wakeup_next_is_b"
        agents = ["alice", "bob", "charlie"]
        room_key = f"{room_name}@{TEAM}"
        await self.create_room(TEAM, room_name, agents, max_rounds=1)
        room = roomService.get_room_by_key(room_key)
        assert await room.activate_scheduling()

        alice_id = await self._get_agent_id("alice")
        bob_id = await self._get_agent_id("bob")
        charlie_id = await self._get_agent_id("charlie")

        # 跑完 1 轮进入 IDLE
        with patch("service.messageBus.publish"):
            await room.add_message(alice_id, "hello")
            await room.handle_finish_request(alice_id)
            await room.add_message(bob_id, "hi")
            await room.handle_finish_request(bob_id)
            await room.add_message(charlie_id, "hey")
            await room.handle_finish_request(charlie_id)

        assert room.state == RoomState.IDLE

        # alice 在房间主动发言，应直接唤醒并调度 bob（alice 的下一位）
        with patch("service.messageBus.publish") as mock_publish:
            await room.add_message(alice_id, "a new topic")

            assert room.state == RoomState.SCHEDULING
            assert room.get_current_turn_agent_id() == bob_id

            turn_calls = [
                c for c in mock_publish.call_args_list
                if c[0][0] == MessageBusTopic.ROOM_STATUS_CHANGED
                and c[1].get("need_scheduling")
            ]
            assert len(turn_calls) == 1
            assert turn_calls[0][1]["current_turn_agent_id"] == bob_id

    async def test_sliding_window_skip_stop(self):
        """
        测试点：滑动窗口跳过判定。
        当所有 AI Agent 自上次发言以来都至少跳过一次，立即停止调度（无需等到本轮结束）。
        场景：Alice 发言 -> Alice 结束 -> Bob 跳过 -> Charlie 跳过 -> (下一轮) Alice 跳过 -> 立即停止。
        """
        room_name = "test_sliding"
        agents = ["alice", "bob", "charlie"]
        await self.create_room(TEAM, room_name, agents, max_rounds=10)
        room = roomService.get_room_by_key(f"{room_name}@{TEAM}")
        assert await room.activate_scheduling()

        alice_id = await self._get_agent_id("alice")
        bob_id = await self._get_agent_id("bob")
        charlie_id = await self._get_agent_id("charlie")
        with patch("service.messageBus.publish"):
            # 1. Alice 发言
            await room.add_message(alice_id, "hello")
            await room.handle_finish_request(alice_id) # pos -> 1 (bob)

            # 2. Bob 跳过
            await room.handle_finish_request(bob_id) # pos -> 2 (charlie), skipped={bob}
            assert room.state == RoomState.SCHEDULING

            # 3. Charlie 跳过
            await room.handle_finish_request(charlie_id) # pos -> 0 (alice), index -> 1, skipped={bob, charlie}
            assert room.state == RoomState.SCHEDULING

            # 4. Alice 跳过
            # 此时 AI 成员全员自上次消息以来都已跳过，应立即停止，不再分发给 Bob
            await room.handle_finish_request(alice_id) # pos -> 1 (bob), index -> 1, skipped={bob, charlie, alice}

        assert room.state == RoomState.IDLE
        assert gtAgentManager.get_agent_name(room.get_current_turn_agent_id()) == "alice"
        assert room._round_count == 1

    async def test_private_room_wakeup_by_index_0(self):
        """
        测试点：私聊房间从 IDLE 唤醒时，如果发消息的人刚好是 index=0，
        不应该被旧的 _should_stop 逻辑错误拦截，而是应该通过 _should_skip 顺延给另一个人。
        """
        room_name = "test_private_wakeup"
        # alice 会是 index 0, bob 会是 index 1
        agents = ["alice", "bob"]
        await self.create_room(TEAM, room_name, agents, room_type=RoomType.PRIVATE)
        room = roomService.get_room_by_key(f"{room_name}@{TEAM}")
        
        # 强制将房间置于 IDLE 状态
        room._scheduler._state = RoomState.IDLE
        
        alice_id = await self._get_agent_id("alice")
        bob_id = await self._get_agent_id("bob")
        
        with patch("service.messageBus.publish") as mock_publish:
            # alice (index 0) 发送消息唤醒房间
            await room.add_message(alice_id, "hello bob")
            
            # 预期：房间应该被唤醒，且下一个轮到的是 bob
            assert room.state == RoomState.SCHEDULING
            assert room.get_current_turn_agent_id() == bob_id
            
            # 确认发布了正确的状态
            scheduling_calls = [
                c for c in mock_publish.call_args_list
                if c[0][0] == MessageBusTopic.ROOM_STATUS_CHANGED
                and c[1].get("need_scheduling")
            ]
            assert len(scheduling_calls) == 1
            assert scheduling_calls[0][1]["current_turn_agent_id"] == bob_id
