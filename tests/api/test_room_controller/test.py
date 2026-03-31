import asyncio
import os
import sys
import time

import aiohttp
import pytest
from constants import RoomType, SpecialAgent

from ...base import ServiceTestCase

_TEAM = "e2e"
_V6_TEAM = "v6test"

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


class _ApiServiceCase(ServiceTestCase):
    """API 测试基类：每个测试类在独立子进程中启动后端与 MockLLM。"""


class TestRoomController(_ApiServiceCase):
    """测试 RoomListHandler 和 RoomMessagesHandler，使用默认配置。"""

    requires_backend = True
    requires_mock_llm = True

    async def _get_room_id(self, room_name: str, team_name: str) -> int:
        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/rooms/list.json") as resp:
                assert resp.status == 200
                data = await resp.json()
        room = next(r for r in data["rooms"] if r["room_name"] == room_name and r["team_name"] == team_name)
        return room["room_id"]

    async def test_get_rooms(self):
        """验证 GET /rooms 返回正确的房间列表及字段结构。"""
        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/rooms/list.json") as resp:
                assert resp.status == 200
                data = await resp.json()
        assert "rooms" in data
        assert len(data["rooms"]) > 0
        room = data["rooms"][0]
        assert "room_id" in room
        assert "room_name" in room
        assert "team_name" in room
        assert "state" in room
        assert "agent_ids" in room

    async def test_get_room_messages(self):
        """验证 GET /rooms/{id}/messages 返回消息列表及元数据字段。"""
        async with aiohttp.ClientSession() as client:
            async with client.get(
                f"{self.backend_base_url}/rooms/{await self._get_room_id('general', _TEAM)}/messages/list.json"
            ) as resp:
                assert resp.status == 200
                data = await resp.json()
        assert "messages" in data
        assert "room_id" in data
        assert "room_name" in data
        assert "team_name" in data
        assert len(data["messages"]) > 0
        msg = data["messages"][0]
        assert "sender" in msg
        assert "content" in msg
        assert "time" in msg

    async def test_room_not_found(self):
        """验证请求不存在的房间时返回 404。"""
        async with aiohttp.ClientSession() as client:
            async with client.get(
                f"{self.backend_base_url}/rooms/999999999/messages/list.json"
            ) as resp:
                assert resp.status in (400, 404)

    async def test_post_message(self):
        """验证 POST /rooms/{id}/messages 将消息写入房间。"""
        room_id = await self._get_room_id("general", _TEAM)
        payload = {"content": "Hello from operator."}
        async with aiohttp.ClientSession() as client:
            async with client.post(
                f"{self.backend_base_url}/rooms/{room_id}/messages/send.json", json=payload
            ) as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["status"] == "ok"

            async with client.get(
                f"{self.backend_base_url}/rooms/{room_id}/messages/list.json"
            ) as resp:
                assert resp.status == 200
                data = await resp.json()
        messages = data["messages"]
        # Operator 的消息应被真正落库，而不仅仅返回 HTTP 成功。
        assert any(
            SpecialAgent.value_of(m["sender"]) == SpecialAgent.OPERATOR and m["content"] == payload["content"]
            for m in messages
        )


class TestRoomControllerPrivate(_ApiServiceCase):
    """测试 v6 新增的 room_type 字段及私有房间行为，使用自定义配置。"""

    requires_backend = True
    requires_mock_llm = True
    use_custom_config = True

    async def _get_room_id(self, room_name: str, team_name: str) -> int:
        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/rooms/list.json") as resp:
                assert resp.status == 200
                data = await resp.json()
        room = next(r for r in data["rooms"] if r["room_name"] == room_name and r["team_name"] == team_name)
        return room["room_id"]

    async def test_room_types_in_list(self):
        """验证 GET /rooms 正确返回 room_type 和 team_name 字段。"""
        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/rooms/list.json") as resp:
                assert resp.status == 200
                data = await resp.json()

        rooms = [room for room in data["rooms"] if room["team_name"] == _V6_TEAM]
        assert len(rooms) == 2

        private_room = next(r for r in rooms if r["room_name"] == "alice_private")
        assert RoomType.value_of(private_room["room_type"]) == RoomType.PRIVATE
        assert private_room["team_name"] == _V6_TEAM
        assert any(agent_id == int(SpecialAgent.OPERATOR.value) for agent_id in private_room["agent_ids"])

        group_room = next(r for r in rooms if r["room_name"] == "public_group")
        assert RoomType.value_of(group_room["room_type"]) == RoomType.GROUP
        assert group_room["team_name"] == _V6_TEAM
        assert not any(agent_id == int(SpecialAgent.OPERATOR.value) for agent_id in group_room["agent_ids"])


    async def test_post_message_to_private_room(self):
        """验证向 private 房间发送消息后，Operator 消息入库且 Agent 在限时内回复。"""
        room_id = await self._get_room_id("alice_private", _V6_TEAM)
        payload = {"content": "Hello Alice, I am the operator."}

        async with aiohttp.ClientSession() as client:
            async with client.post(
                f"{self.backend_base_url}/rooms/{room_id}/messages/send.json", json=payload
            ) as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["status"] == "ok"

            async with client.get(f"{self.backend_base_url}/rooms/{room_id}/messages/list.json") as resp:
                assert resp.status == 200
                data = await resp.json()
                messages = data["messages"]
                assert messages[1]["content"] == payload["content"]
                assert SpecialAgent.value_of(messages[1]["sender"]) == SpecialAgent.OPERATOR

        max_wait = 15
        start_time = time.time()
        messages = []
        while time.time() - start_time < max_wait:
            async with aiohttp.ClientSession() as client:
                async with client.get(
                    f"{self.backend_base_url}/rooms/{room_id}/messages/list.json"
                ) as resp:
                    assert resp.status == 200
                    data = await resp.json()
                    messages = data["messages"]
                    # Agent 回复由调度异步触发，使用轮询等待可观测结果。
                    if any(m["sender"] == "alice" for m in messages):
                        break
            await asyncio.sleep(0.1)
        else:
            pytest.fail("Agent Alice 未能在限时内回复 Operator")

        alice_msg = next(m for m in messages if m["sender"] == "alice")
        assert len(alice_msg["content"]) > 0
