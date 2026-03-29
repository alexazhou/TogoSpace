"""integration tests for service.messageBus state transitions"""
import asyncio
import os
import sys

import pytest

import service.messageBus as messageBus
from service.messageBus import Message
from constants import MessageBusTopic
from ...base import ServiceTestCase

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")



class TestmessageBus(ServiceTestCase):
    async def test_subscribe_and_publish(self):
        """订阅后发布消息，订阅者应收到 Message 对象及原始 payload。"""
        received = []
        messageBus.subscribe(MessageBusTopic.ROOM_MEMBER_TURN, lambda m: received.append(m))
        messageBus.publish(MessageBusTopic.ROOM_MEMBER_TURN, member_name="alice", room_name="r1")
        await asyncio.sleep(0)
        assert len(received) == 1
        assert isinstance(received[0], Message)
        assert received[0].payload["member_name"] == "alice"
        assert received[0].payload["room_name"] == "r1"

    async def test_multiple_subscribers_all_called(self):
        """同一 topic 的多个订阅者应按注册顺序都被调用。"""
        calls = []
        messageBus.subscribe(MessageBusTopic.ROOM_MEMBER_TURN, lambda m: calls.append("a"))
        messageBus.subscribe(MessageBusTopic.ROOM_MEMBER_TURN, lambda m: calls.append("b"))
        messageBus.publish(MessageBusTopic.ROOM_MEMBER_TURN, member_name="x", room_name="y")
        await asyncio.sleep(0)
        assert calls == ["a", "b"]

    async def test_no_subscribers_no_error(self):
        """没有订阅者时发布消息不应抛异常。"""
        messageBus.publish(MessageBusTopic.ROOM_MEMBER_TURN, member_name="x", room_name="y")

    async def test_failing_subscriber_does_not_block_others(self):
        """单个订阅者异常不应阻断其他订阅者。"""
        calls = []
        messageBus.subscribe(MessageBusTopic.ROOM_MEMBER_TURN, lambda m: (_ for _ in ()).throw(RuntimeError("boom")))
        messageBus.subscribe(MessageBusTopic.ROOM_MEMBER_TURN, lambda m: calls.append("ok"))
        messageBus.publish(MessageBusTopic.ROOM_MEMBER_TURN, member_name="x", room_name="y")
        await asyncio.sleep(0)
        assert "ok" in calls

    async def test_stop_clears_subscribers(self):
        """shutdown 后已注册订阅者应全部清空。"""
        received = []
        messageBus.subscribe(MessageBusTopic.ROOM_MEMBER_TURN, lambda m: received.append(m))
        messageBus.shutdown()
        messageBus.publish(MessageBusTopic.ROOM_MEMBER_TURN, member_name="x", room_name="y")
        await asyncio.sleep(0)
        assert len(received) == 0

    async def test_init_clears_subscribers(self):
        """startup 会重置订阅表，避免历史订阅泄露到新场景。"""
        received = []
        messageBus.subscribe(MessageBusTopic.ROOM_MEMBER_TURN, lambda m: received.append(m))
        await messageBus.startup()
        messageBus.publish(MessageBusTopic.ROOM_MEMBER_TURN, member_name="x", room_name="y")
        assert len(received) == 0

    async def test_publish_in_running_loop_is_deferred(self):
        """在事件循环中 publish 应异步调度回调，避免阻塞当前发布链路。"""
        received = []
        messageBus.subscribe(MessageBusTopic.ROOM_MEMBER_TURN, lambda m: received.append(m))
        messageBus.publish(MessageBusTopic.ROOM_MEMBER_TURN, member_name="x", room_name="y")
        assert len(received) == 0
        await asyncio.sleep(0)
        assert len(received) == 1

    async def test_topic_isolation(self):
        """不同 topic 互不干扰"""
        received = []
        messageBus.subscribe(MessageBusTopic.MEMBER_STATUS_CHANGED, lambda m: received.append(m))
        messageBus.publish(MessageBusTopic.ROOM_MEMBER_TURN, member_name="x", room_name="y")
        await asyncio.sleep(0)
        assert len(received) == 0
