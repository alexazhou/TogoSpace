import pytest
from unittest.mock import patch, MagicMock
from service import room_service
from constants import RoomType, RoomState, MessageBusTopic


@pytest.fixture(autouse=True)
def cleanup_rooms():
    """每个测试前后清理房间数据，确保测试环境隔离。"""
    room_service.close_all()
    yield
    room_service.close_all()


def test_strict_turn_advancement():
    """
    测试点：严格顺序推进逻辑
    场景描述：
    1. 验证只有当消息发送者是当前轮次“指定”的发言人时，轮次索引才增加。
    2. 验证非当前发言人的“插话”虽然被记录，但不会导致轮次位置发生偏移。
    """
    room_name = "test_room"
    agents = ["alice", "bob", "charlie"]
    room_service.init(room_name, agents, room_type=RoomType.GROUP)
    room = room_service.get_room(room_name)
    room.setup_turns(agents, max_turns=10)

    # 初始状态：应该轮到 alice 发言
    assert room.get_current_turn_agent() == "alice"
    assert room._turn_pos == 0

    # 动作 1：alice 正常发言
    # 预期：轮次推进到 bob
    with patch("service.message_bus.publish") as mock_publish:
        room.add_message("alice", "hello")
        assert room.get_current_turn_agent() == "bob"
        assert room._turn_pos == 1
        # 验证系统发布了指派 bob 发言的事件
        mock_publish.assert_any_call(MessageBusTopic.ROOM_AGENT_TURN, agent_name="bob", room_name=room_name)

    # 动作 2：charlie 在不该他说话的时候插话
    # 预期：消息被存入，但轮次依然停留在 bob，且不发布新的 turn 事件
    with patch("service.message_bus.publish") as mock_publish:
        room.add_message("charlie", "I am interrupting")
        assert room.get_current_turn_agent() == "bob"
        assert room._turn_pos == 1
        # 确认仅发布了消息增加事件
        topics = [call[0][0] for call in mock_publish.call_args_list]
        assert MessageBusTopic.ROOM_MSG_ADDED in topics
        assert MessageBusTopic.ROOM_AGENT_TURN not in topics

    # 动作 3：bob 正常发言
    # 预期：轮次顺利推进到 charlie
    room.add_message("bob", "responding to alice")
    assert room.get_current_turn_agent() == "charlie"
    assert room._turn_pos == 2


def test_skip_turn_validation():
    """
    测试点：跳过发言的身份校验
    场景描述：
    1. 验证非当前发言人尝试执行“跳过”动作会被拒绝。
    2. 验证当前合法发言人可以成功触发“跳过”。
    """
    room_name = "test_skip"
    agents = ["alice", "bob"]
    room_service.init(room_name, agents)
    room = room_service.get_room(room_name)
    room.setup_turns(agents, max_turns=10)

    # 动作 1：bob 尝试跳过本属于 alice 的回合
    # 预期：操作被拒绝，轮次依然是 alice
    room.skip_turn(sender="bob")
    assert room.get_current_turn_agent() == "alice"

    # 动作 2：alice 正常执行跳过
    # 预期：成功推进到 bob
    room.skip_turn(sender="alice")
    assert room.get_current_turn_agent() == "bob"


def test_idle_wakeup_logic():
    """
    测试点：最大轮次限制后的唤醒机制
    场景描述：
    1. 验证房间达到最大轮次后进入 IDLE 状态。
    2. 验证在 IDLE 状态下，哪怕是“插话”也能重新激活房间并重置轮次计数。
    """
    room_name = "test_idle"
    agents = ["alice", "bob"]
    room_service.init(room_name, agents) 
    room = room_service.get_room(room_name)
    room.setup_turns(agents, max_turns=1) # 设置最大轮次为 1

    # alice 和 bob 各说一句，完成 1 个全循环
    room.add_message("alice", "hi")
    room.add_message("bob", "bye")

    # 验证房间进入休眠
    assert room.state == RoomState.IDLE
    assert room._turn_index == 1
    assert room.get_current_turn_agent() == "alice"

    # 动作：处于休眠状态时，bob 突然又发了一条消息（插话）
    # 预期：房间被唤醒，计数器重置为 0，并重新向当前本该发言的人 (alice) 发布 turn 事件
    with patch("service.message_bus.publish") as mock_publish:
        room.add_message("bob", "wait, one more thing")
        
        assert room.state == RoomState.SCHEDULING
        assert room._turn_index == 0
        assert room.get_current_turn_agent() == "alice"
        
        # 关键：验证系统重新激活了本该说话的 alice
        mock_publish.assert_any_call(MessageBusTopic.ROOM_AGENT_TURN, agent_name="alice", room_name=room_name)


def test_full_loop_advancement():
    """
    测试点：完整轮次计数逻辑
    场景描述：
    验证 _turn_index 只有在列表中所有成员都发言过一次后才增加。
    """
    room_name = "test_loop"
    agents = ["a", "b"]
    room_service.init(room_name, agents)
    room = room_service.get_room(room_name)
    room.setup_turns(agents, max_turns=5)

    assert room._turn_index == 0
    
    # 动作 1：第一个人发言
    # 预期：index 依然是 0
    room.add_message("a", "1")
    assert room._turn_index == 0
    
    # 动作 2：第二个人发言（最后一个人）
    # 预期：index 进位变成 1，pos 回归 0
    room.add_message("b", "2")
    assert room._turn_index == 1
    assert room._turn_pos == 0
    assert room.get_current_turn_agent() == "a"
