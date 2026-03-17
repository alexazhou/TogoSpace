"""unit tests for service.message_bus"""
import service.message_bus as message_bus
from service.message_bus import Message
from constants import MessageBusTopic
from ...base import ServiceTestCase


class TestMessageBus(ServiceTestCase):
    def test_subscribe_and_publish(self):
        """订阅后发布消息，订阅者应收到 Message 对象及原始 payload。"""
        received = []
        message_bus.subscribe(MessageBusTopic.ROOM_AGENT_TURN, lambda m: received.append(m))
        message_bus.publish(MessageBusTopic.ROOM_AGENT_TURN, agent_name="alice", room_name="r1")
        assert len(received) == 1
        assert isinstance(received[0], Message)
        assert received[0].payload["agent_name"] == "alice"
        assert received[0].payload["room_name"] == "r1"

    def test_multiple_subscribers_all_called(self):
        """同一 topic 的多个订阅者应按注册顺序都被调用。"""
        calls = []
        message_bus.subscribe(MessageBusTopic.ROOM_AGENT_TURN, lambda m: calls.append("a"))
        message_bus.subscribe(MessageBusTopic.ROOM_AGENT_TURN, lambda m: calls.append("b"))
        message_bus.publish(MessageBusTopic.ROOM_AGENT_TURN, agent_name="x", room_name="y")
        assert calls == ["a", "b"]

    def test_no_subscribers_no_error(self):
        """没有订阅者时发布消息不应抛异常。"""
        message_bus.publish(MessageBusTopic.ROOM_AGENT_TURN, agent_name="x", room_name="y")

    def test_failing_subscriber_does_not_block_others(self):
        """单个订阅者异常不应阻断其他订阅者。"""
        calls = []
        message_bus.subscribe(MessageBusTopic.ROOM_AGENT_TURN, lambda m: (_ for _ in ()).throw(RuntimeError("boom")))
        message_bus.subscribe(MessageBusTopic.ROOM_AGENT_TURN, lambda m: calls.append("ok"))
        message_bus.publish(MessageBusTopic.ROOM_AGENT_TURN, agent_name="x", room_name="y")
        assert "ok" in calls

    def test_stop_clears_subscribers(self):
        """shutdown 后已注册订阅者应全部清空。"""
        received = []
        message_bus.subscribe(MessageBusTopic.ROOM_AGENT_TURN, lambda m: received.append(m))
        message_bus.shutdown()
        message_bus.publish(MessageBusTopic.ROOM_AGENT_TURN, agent_name="x", room_name="y")
        assert len(received) == 0

    async def test_init_clears_subscribers(self):
        """startup 会重置订阅表，避免历史订阅泄露到新场景。"""
        received = []
        message_bus.subscribe(MessageBusTopic.ROOM_AGENT_TURN, lambda m: received.append(m))
        await message_bus.startup()
        message_bus.publish(MessageBusTopic.ROOM_AGENT_TURN, agent_name="x", room_name="y")
        assert len(received) == 0

    def test_topic_isolation(self):
        """不同 topic 互不干扰"""
        received = []
        message_bus.subscribe("other.topic", lambda m: received.append(m))
        message_bus.publish(MessageBusTopic.ROOM_AGENT_TURN, agent_name="x", room_name="y")
        assert len(received) == 0
