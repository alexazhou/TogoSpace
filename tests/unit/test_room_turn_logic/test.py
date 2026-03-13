import pytest
from unittest.mock import patch, MagicMock
from service import room_service
from constants import RoomType, RoomState, MessageBusTopic

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
    room_service.create_room(TEAM, room_name, agents, room_type=RoomType.GROUP)
    room_key = f"{room_name}@{TEAM}"
    room = room_service.get_room(room_key)
    room.setup_turns(agents, max_turns=10)

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
    room_service.create_room(TEAM, room_name, agents)
    room = room_service.get_room(f"{room_name}@{TEAM}")
    room.setup_turns(agents, max_turns=10)

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
    room_service.create_room(TEAM, room_name, agents)
    room_key = f"{room_name}@{TEAM}"
    room = room_service.get_room(room_key)
    room.setup_turns(agents, max_turns=1)

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
    room_service.create_room(TEAM, room_name, agents)
    room = room_service.get_room(f"{room_name}@{TEAM}")
    room.setup_turns(agents, max_turns=5)

    assert room._turn_index == 0

    room.add_message("a", "1")
    assert room._turn_index == 0

    room.add_message("b", "2")
    assert room._turn_index == 1
    assert room._turn_pos == 0
    assert room.get_current_turn_agent() == "a"
