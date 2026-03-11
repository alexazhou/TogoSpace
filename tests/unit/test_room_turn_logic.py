import pytest
from unittest.mock import patch, MagicMock
from service import room_service
from constants import RoomType, RoomState, MessageBusTopic


@pytest.fixture(autouse=True)
def cleanup_rooms():
    room_service.close_all()
    yield
    room_service.close_all()


def test_strict_turn_advancement():
    """验证只有当前预期发言人说话时，轮次才会推进。"""
    room_name = "test_room"
    agents = ["alice", "bob", "charlie"]
    room_service.init(room_name, agents, room_type=RoomType.GROUP)
    room = room_service.get_room(room_name)
    room.setup_turns(agents, max_turns=10)

    assert room.get_current_turn_agent() == "alice"
    assert room._turn_pos == 0

    # 1. alice 说话 -> 应该推进
    with patch("service.message_bus.publish") as mock_publish:
        room.add_message("alice", "hello")
        assert room.get_current_turn_agent() == "bob"
        assert room._turn_pos == 1
        # 验证发布了针对 bob 的 turn 事件
        mock_publish.assert_any_call(MessageBusTopic.ROOM_AGENT_TURN, agent_name="bob", room_name=room_name)

    # 2. charlie 插话 -> 不应该推进
    with patch("service.message_bus.publish") as mock_publish:
        room.add_message("charlie", "I am interrupting")
        assert room.get_current_turn_agent() == "bob"
        assert room._turn_pos == 1
        # 验证发送了消息增加事件，但没有发布新的 turn 事件（除非是从 IDLE 唤醒）
        topics = [call[0][0] for call in mock_publish.call_args_list]
        assert MessageBusTopic.ROOM_MSG_ADDED in topics
        assert MessageBusTopic.ROOM_AGENT_TURN not in topics

    # 3. bob 说话 -> 应该推进到 charlie
    room.add_message("bob", "responding to alice")
    assert room.get_current_turn_agent() == "charlie"
    assert room._turn_pos == 2


def test_skip_turn_validation():
    """验证 skip_turn 的校验逻辑。"""
    room_name = "test_skip"
    agents = ["alice", "bob"]
    room_service.init(room_name, agents)
    room = room_service.get_room(room_name)
    room.setup_turns(agents, max_turns=10)

    # bob 尝试跳过 alice 的轮次 -> 应该被拒绝
    room.skip_turn(sender="bob")
    assert room.get_current_turn_agent() == "alice"

    # alice 自己跳过 -> 成功推进
    room.skip_turn(sender="alice")
    assert room.get_current_turn_agent() == "bob"


def test_idle_wakeup_logic():
    """验证达到最大轮次后的唤醒逻辑。"""
    room_name = "test_idle"
    agents = ["alice", "bob"]
    room_service.init(room_name, agents) 
    room = room_service.get_room(room_name)
    room.setup_turns(agents, max_turns=1) # 设为 1 轮

    # 第一轮：alice, bob 各说一句
    room.add_message("alice", "hi")
    room.add_message("bob", "bye")

    assert room.state == RoomState.IDLE
    assert room._turn_index == 1
    assert room.get_current_turn_agent() == "alice" # 逻辑上回到了 0

    # 此时任何活动应该唤醒房间
    with patch("service.message_bus.publish") as mock_publish:
        room.add_message("bob", "wait, one more thing") # bob 插话（当前轮到 alice）
        
        assert room.state == RoomState.SCHEDULING
        assert room._turn_index == 0
        assert room.get_current_turn_agent() == "alice" # 依然轮到 alice
        
        # 验证重新激活了 alice
        mock_publish.assert_any_call(MessageBusTopic.ROOM_AGENT_TURN, agent_name="alice", room_name=room_name)


def test_full_loop_advancement():
    """验证完整的一轮全员发言后 index 增加。"""
    room_name = "test_loop"
    agents = ["a", "b"]
    room_service.init(room_name, agents)
    room = room_service.get_room(room_name)
    room.setup_turns(agents, max_turns=5)

    assert room._turn_index == 0
    
    room.add_message("a", "1")
    assert room._turn_index == 0
    
    room.add_message("b", "2")
    assert room._turn_index == 1
    assert room._turn_pos == 0
    assert room.get_current_turn_agent() == "a"
