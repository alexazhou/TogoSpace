import pytest
from unittest.mock import patch, MagicMock, call
from service import room_service
from constants import RoomType, RoomState, MessageBusTopic, SpecialAgent

TEAM = "test_team"


@pytest.fixture(autouse=True)
def cleanup_rooms():
    """每个测试前后清理房间数据，确保测试环境隔离。"""
    room_service.shutdown()
    room_service.startup()
    yield
    room_service.shutdown()


def test_strict_turn_advancement():
    """
    测试点：严格顺序推进逻辑
    """
    room_name = "test_room"
    agents = ["alice", "bob", "charlie"]
    room_key = f"{room_name}@{TEAM}"
    room_service.create_room(TEAM, room_name, agents, room_type=RoomType.GROUP, max_turns=10)
    room = room_service.get_room(room_key)

    assert room.get_current_turn_agent() == "alice"
    assert room._turn_pos == 0

    with patch("service.message_bus.publish") as mock_publish:
        room.add_message("alice", "hello")
        assert room.get_current_turn_agent() == "bob"
        assert room._turn_pos == 1
        mock_publish.assert_any_call(
            MessageBusTopic.ROOM_AGENT_TURN,
            agent_name="bob",
            room_name=room_name,
            room_key=room_key,
            team_name=TEAM,
        )

    with patch("service.message_bus.publish") as mock_publish:
        room.add_message("charlie", "I am interrupting")
        assert room.get_current_turn_agent() == "bob"
        assert room._turn_pos == 1
        topics = [call[0][0] for call in mock_publish.call_args_list]
        assert MessageBusTopic.ROOM_MSG_ADDED in topics
        assert MessageBusTopic.ROOM_AGENT_TURN not in topics

    room.add_message("bob", "responding to alice")
    assert room.get_current_turn_agent() == "charlie"
    assert room._turn_pos == 2


def test_skip_turn_validation():
    """
    测试点：跳过发言的身份校验
    """
    room_name = "test_skip"
    agents = ["alice", "bob"]
    room_service.create_room(TEAM, room_name, agents, max_turns=10)
    room = room_service.get_room(f"{room_name}@{TEAM}")

    room.skip_turn(sender="bob")
    assert room.get_current_turn_agent() == "alice"

    room.skip_turn(sender="alice")
    assert room.get_current_turn_agent() == "bob"


def test_idle_wakeup_logic():
    """
    测试点：最大轮次限制后的唤醒机制
    """
    room_name = "test_idle"
    agents = ["alice", "bob"]
    room_key = f"{room_name}@{TEAM}"
    room_service.create_room(TEAM, room_name, agents, max_turns=1)
    room = room_service.get_room(room_key)

    room.add_message("alice", "hi")
    room.add_message("bob", "bye")

    assert room.state == RoomState.IDLE
    assert room._turn_index == 1
    assert room.get_current_turn_agent() == "alice"

    with patch("service.message_bus.publish") as mock_publish:
        room.add_message("bob", "wait, one more thing")

        assert room.state == RoomState.SCHEDULING
        assert room._turn_index == 0
        assert room.get_current_turn_agent() == "alice"

        mock_publish.assert_any_call(
            MessageBusTopic.ROOM_AGENT_TURN,
            agent_name="alice",
            room_name=room_name,
            room_key=room_key,
            team_name=TEAM,
        )


def test_full_loop_advancement():
    """
    测试点：完整轮次计数逻辑
    """
    room_name = "test_loop"
    agents = ["a", "b"]
    room_service.create_room(TEAM, room_name, agents, max_turns=5)
    room = room_service.get_room(f"{room_name}@{TEAM}")

    assert room._turn_index == 0

    room.add_message("a", "1")
    assert room._turn_index == 0

    room.add_message("b", "2")
    assert room._turn_index == 1
    assert room._turn_pos == 0
    assert room.get_current_turn_agent() == "a"


# ──────────────────────────────────────────────────────────────────────────────
# 全员跳过时停止调度
# ──────────────────────────────────────────────────────────────────────────────

def test_all_skip_stops_scheduling():
    """
    测试点：同一轮内所有 AI Agent 均调用 skip_turn，本轮结束后房间立即进入 IDLE。
    """
    room_name = "skip_all"
    agents = ["alice", "bob"]
    room_service.create_room(TEAM, room_name, agents, max_turns=10)
    room = room_service.get_room(f"{room_name}@{TEAM}")

    assert room.state == RoomState.SCHEDULING

    with patch("service.message_bus.publish"):
        room.skip_turn(sender="alice")
        # 仅 alice 跳过，bob 尚未发言 → 仍在调度
        assert room.state == RoomState.SCHEDULING

        room.skip_turn(sender="bob")
        # alice + bob 均跳过，本轮结束 → IDLE
        assert room.state == RoomState.IDLE


def test_all_skip_no_further_turn_events():
    """
    测试点：全员跳过进入 IDLE 后，不再发布 ROOM_AGENT_TURN 事件。
    """
    room_name = "skip_no_event"
    agents = ["alice", "bob"]
    room_service.create_room(TEAM, room_name, agents, max_turns=10)
    room = room_service.get_room(f"{room_name}@{TEAM}")

    with patch("service.message_bus.publish") as mock_publish:
        room.skip_turn(sender="alice")
        room.skip_turn(sender="bob")

        turn_calls = [
            c for c in mock_publish.call_args_list
            if c[0][0] == MessageBusTopic.ROOM_AGENT_TURN
        ]
        # create_room 时已发布 alice 的初始事件（在 mock 外），
        # mock 内：skip alice → bob 事件，skip bob → 全员跳过，不再发布
        agent_names_notified = [c[1]["agent_name"] for c in turn_calls]
        assert agent_names_notified == ["bob"]


def test_all_skip_wakeup_based_on_state_not_turn_index():
    """
    测试点：全员跳过进入 IDLE 时，_turn_index 不会被人为抬高到 _max_turns；
    唤醒逻辑只依赖房间状态（IDLE），与 _turn_index 无关。
    """
    room_name = "skip_idx"
    agents = ["alice", "bob"]
    room_key = f"{room_name}@{TEAM}"
    room_service.create_room(TEAM, room_name, agents, max_turns=10)
    room = room_service.get_room(room_key)

    with patch("service.message_bus.publish"):
        room.skip_turn(sender="alice")
        room.skip_turn(sender="bob")

    assert room.state == RoomState.IDLE
    # _turn_index 应为自然推进值（1），不被强制拉到 _max_turns
    assert room._turn_index == 1
    assert room._turn_index < room._max_turns

    # 即便 _turn_index 远小于 _max_turns，发消息依然能唤醒房间
    with patch("service.message_bus.publish"):
        room.add_message("alice", "back")

    assert room.state == RoomState.SCHEDULING
    assert room._turn_index == 0


def test_all_skip_wakeup_by_operator():
    """
    测试点：全员跳过进入 IDLE 后，Operator 发一条消息能重新唤醒调度。
    """
    room_name = "skip_wakeup"
    agents = [SpecialAgent.OPERATOR, "alice", "bob"]
    room_key = f"{room_name}@{TEAM}"
    room_service.create_room(TEAM, room_name, agents, max_turns=10)
    room = room_service.get_room(room_key)

    with patch("service.message_bus.publish"):
        # operator 发言推进到 alice
        room.add_message(SpecialAgent.OPERATOR, "start")
        room.skip_turn(sender="alice")
        room.skip_turn(sender="bob")

    assert room.state == RoomState.IDLE

    with patch("service.message_bus.publish") as mock_publish:
        room.add_message(SpecialAgent.OPERATOR, "wake up")

        assert room.state == RoomState.SCHEDULING
        assert room._turn_index == 0

        # 应重新发布当前发言人的 TURN 事件
        turn_calls = [
            c for c in mock_publish.call_args_list
            if c[0][0] == MessageBusTopic.ROOM_AGENT_TURN
        ]
        assert len(turn_calls) >= 1


def test_partial_skip_does_not_stop():
    """
    测试点：只有部分 Agent 跳过时，调度不停止，房间继续推进。
    """
    room_name = "skip_partial"
    agents = ["alice", "bob", "charlie"]
    room_service.create_room(TEAM, room_name, agents, max_turns=10)
    room = room_service.get_room(f"{room_name}@{TEAM}")

    with patch("service.message_bus.publish"):
        room.skip_turn(sender="alice")   # alice 跳过
        room.add_message("bob", "hi")    # bob 正常发言
        room.skip_turn(sender="charlie") # charlie 跳过

    # 本轮 bob 发了言，不是全员跳过 → 轮次正常推进，房间仍在调度
    assert room.state == RoomState.SCHEDULING
    assert room._turn_index == 1


def test_operator_excluded_from_skip_check():
    """
    测试点：房间含 Operator 时，全员跳过判定仅针对 AI Agent，
    即使 Operator 未跳过，其余 AI Agent 全部跳过也应触发停止。
    """
    room_name = "skip_op"
    agents = [SpecialAgent.OPERATOR, "alice", "bob"]
    room_service.create_room(TEAM, room_name, agents, max_turns=10)
    room = room_service.get_room(f"{room_name}@{TEAM}")

    with patch("service.message_bus.publish"):
        # Operator 正常发言（推进到 alice）
        room.add_message(SpecialAgent.OPERATOR, "hello")
        # 两个 AI Agent 均跳过
        room.skip_turn(sender="alice")
        room.skip_turn(sender="bob")

    # Operator 未跳过，但 AI 全员跳过 → 应停止调度
    assert room.state == RoomState.IDLE


def test_skip_set_resets_each_round():
    """
    测试点：每轮的跳过记录互不干扰——第一轮全员跳过停止后唤醒，
    第二轮部分跳过不应再次停止。
    """
    room_name = "skip_reset"
    agents = ["alice", "bob"]
    room_service.create_room(TEAM, room_name, agents, max_turns=10)
    room = room_service.get_room(f"{room_name}@{TEAM}")

    with patch("service.message_bus.publish"):
        # 第一轮：全员跳过 → IDLE
        room.skip_turn(sender="alice")
        room.skip_turn(sender="bob")
    assert room.state == RoomState.IDLE

    with patch("service.message_bus.publish"):
        # alice 发消息唤醒房间，同时推进到 bob
        room.add_message("alice", "I'm back")
    assert room.state == RoomState.SCHEDULING

    with patch("service.message_bus.publish"):
        # 第二轮：只有 bob 跳过，alice 已发言
        room.skip_turn(sender="bob")

    # 第二轮不是全员跳过（alice 正常发言），房间应继续调度
    assert room.state == RoomState.SCHEDULING
