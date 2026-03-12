"""端到端 API 测试——通过真实 HTTP 请求验证后端接口行为。"""
import asyncio
import json
import threading

import aiohttp

from ...base import ServiceTestCase

TEAM = "e2e"


class TestApiE2e(ServiceTestCase):
    requires_backend = True
    requires_mock_llm = True

    ws_events: list = None

    @classmethod
    def setup_class(cls):
        super().setup_class()
        cls._collect_ws_events()

    @classmethod
    def _collect_ws_events(cls):
        """在独立线程中连接 WebSocket，等待至少一条 event=message 推送。"""
        collected: list = []
        ws_done = threading.Event()

        async def _collect():
            ws_url = f"ws://127.0.0.1:{cls.backend_port}/ws/events"
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(ws_url) as ws:
                        try:
                            async with asyncio.timeout(18):
                                async for msg in ws:
                                    if msg.type == aiohttp.WSMsgType.TEXT:
                                        data = json.loads(msg.data)
                                        if data.get("event") == "message":
                                            collected.append(data)
                                            break
                                    elif msg.type in (
                                        aiohttp.WSMsgType.ERROR,
                                        aiohttp.WSMsgType.CLOSED,
                                    ):
                                        break
                        except asyncio.TimeoutError:
                            pass
            except Exception:
                pass
            finally:
                ws_done.set()

        def _thread():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(_collect())
            loop.close()

        threading.Thread(target=_thread, daemon=True).start()
        ws_done.wait(timeout=22)
        cls.ws_events = collected

    async def test_get_agents(self):
        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/agents") as resp:
                assert resp.status == 200
                data = await resp.json()
        assert "agents" in data
        assert len(data["agents"]) > 0
        agent = data["agents"][0]
        assert "name" in agent
        assert "model" in agent
        assert "team_name" in agent

    async def test_get_rooms(self):
        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/rooms") as resp:
                assert resp.status == 200
                data = await resp.json()
        assert "rooms" in data
        assert len(data["rooms"]) > 0
        room = data["rooms"][0]
        assert "room_id" in room
        assert "room_name" in room
        assert "team_name" in room
        assert "state" in room
        assert "members" in room

    async def test_get_room_messages(self):
        async with aiohttp.ClientSession() as client:
            async with client.get(
                f"{self.backend_base_url}/rooms/general@{TEAM}/messages"
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
        async with aiohttp.ClientSession() as client:
            async with client.get(
                f"{self.backend_base_url}/rooms/nonexistent@noexist/messages"
            ) as resp:
                assert resp.status == 404

    async def test_ws_receives_message(self):
        """验证 WebSocket 能接收到 event=message 类型的推送。"""
        assert len(self.ws_events) > 0, "未收到任何 event=message 的 WebSocket 推送"
        event = self.ws_events[0]
        assert event.get("event") == "message"
        assert "room_id" in event
        assert "team_name" in event
        assert "sender" in event
        assert "content" in event
