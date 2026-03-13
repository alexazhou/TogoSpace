import asyncio
import json
import threading

import aiohttp

from ...base import ServiceTestCase

_TEAM = "e2e"


class TestWsController(ServiceTestCase):
    """测试 EventsWsHandler，验证 WebSocket 推送行为。"""

    requires_backend = True
    requires_mock_llm = True

    async def test_ws_receives_message(self):
        """验证 POST 消息后 WebSocket 能收到 event=message 推送，且字段结构正确。"""
        collected = []
        ws_done = threading.Event()

        async def _collect():
            ws_url = f"ws://127.0.0.1:{self.backend_port}/ws/events"
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(ws_url) as ws:
                        async with session.post(
                            f"{self.backend_base_url}/rooms/general@{_TEAM}/messages",
                            json={"content": "Testing WebSocket"},
                        ) as resp:
                            assert resp.status == 200

                        async with asyncio.timeout(5):
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
        ws_done.wait(timeout=10)

        assert len(collected) > 0, "未收到任何 event=message 的 WebSocket 推送"
        event = collected[0]
        assert event.get("event") == "message"
        assert "room_id" in event
        assert "team_name" in event
        assert "sender" in event
        assert "content" in event
