"""integration tests for service.messageBus state transitions"""
import os
import sys

import pytest

import service.messageBus as messageBus
from service.messageBus import Message
from constants import MessageBusTopic
from ...base import ServiceTestCase

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


@pytest.mark.forked
class TestmessageBus(ServiceTestCase):
    def test_subscribe_and_publish(self):
        """订阅后发布消息，订阅者应收到 Message 对象及原始 payload。"""
        received = []
        messageBus.subscribe(MessageBusTopic.ROOM_AGENT_TURN, lambda m: received.append(m))
        messageBus.publish(MessageBusTopic.ROOM_AGENT_TURN, agent_name="alice", room_name="r1")
        assert len(received) == 1
        assert isinstance(received[0], Message)
        assert received[0].payload["agent_name"] == "alice"
        assert received[0].payload["room_name"] == "r1"

    def test_multiple_subscribers_all_called(self):
        """同一 topic 的多个订阅者应按注册顺序都被调用。"""
        calls = []
        messageBus.subscribe(MessageBusTopic.ROOM_AGENT_TURN, lambda m: calls.append("a"))
        messageBus.subscribe(MessageBusTopic.ROOM_AGENT_TURN, lambda m: calls.append("b"))
        messageBus.publish(MessageBusTopic.ROOM_AGENT_TURN, agent_name="x", room_name="y")
        assert calls == ["a", "b"]

    def test_no_subscribers_no_error(self):
        """没有订阅者时发布消息不应抛异常。"""
        messageBus.publish(MessageBusTopic.ROOM_AGENT_TURN, agent_name="x", room_name="y")

    def test_failing_subscriber_does_not_block_others(self):
        """单个订阅者异常不应阻断其他订阅者。"""
        calls = []
        messageBus.subscribe(MessageBusTopic.ROOM_AGENT_TURN, lambda m: (_ for _ in ()).throw(RuntimeError("boom")))
        messageBus.subscribe(MessageBusTopic.ROOM_AGENT_TURN, lambda m: calls.append("ok"))
        messageBus.publish(MessageBusTopic.ROOM_AGENT_TURN, agent_name="x", room_name="y")
        assert "ok" in calls

    def test_stop_clears_subscribers(self):
        """shutdown 后已注册订阅者应全部清空。"""
        received = []
        messageBus.subscribe(MessageBusTopic.ROOM_AGENT_TURN, lambda m: received.append(m))
        messageBus.shutdown()
        messageBus.publish(MessageBusTopic.ROOM_AGENT_TURN, agent_name="x", room_name="y")
        assert len(received) == 0

    async def test_init_clears_subscribers(self):
        """startup 会重置订阅表，避免历史订阅泄露到新场景。"""
        received = []
        messageBus.subscribe(MessageBusTopic.ROOM_AGENT_TURN, lambda m: received.append(m))
        await messageBus.startup()
        messageBus.publish(MessageBusTopic.ROOM_AGENT_TURN, agent_name="x", room_name="y")
        assert len(received) == 0

    def test_topic_isolation(self):
        """不同 topic 互不干扰"""
        received = []
        messageBus.subscribe("other.topic", lambda m: received.append(m))
        messageBus.publish(MessageBusTopic.ROOM_AGENT_TURN, agent_name="x", room_name="y")
        assert len(received) == 0
