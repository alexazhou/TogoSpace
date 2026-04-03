import asyncio
import json
import os
import sys
import threading

import aiohttp
import pytest

from ...base import ServiceTestCase

_TEAM = "e2e"

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


class _ApiServiceCase(ServiceTestCase):
    """API 测试基类：每个测试类在独立子进程中启动后端与 MockLLM。"""


class TestWsController(_ApiServiceCase):
    """测试 EventsWsHandler，验证 WebSocket 推送行为。"""

    requires_backend = True
    requires_mock_llm = True

    async def test_ws_receives_message(self):
        """验证 POST 消息后 WebSocket 能收到 event=message 推送，且字段结构正确。"""
        collected = []
        ws_done = threading.Event()

        async def _collect():
            ws_url = f"ws://127.0.0.1:{self.backend_port}/ws/events.json"
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"{self.backend_base_url}/rooms/list.json") as resp:
                        assert resp.status == 200
                        rooms = (await resp.json())["rooms"]
                    room_id = next(r["room_id"] for r in rooms if r["room_name"] == "general" and r["team_name"] == _TEAM)
                    async with session.ws_connect(ws_url) as ws:
                        async with session.post(
                            f"{self.backend_base_url}/rooms/{room_id}/messages/send.json",
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
            # 在独立线程里跑事件循环，避免与 pytest-asyncio 当前 loop 冲突。
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
        assert "room_key" in event
        assert "team_id" in event
        assert "team_name" in event
        assert "sender" in event
        assert "content" in event

    async def test_ws_agent_status_contains_real_team_id(self):
        """agent_status 事件中的 team_id 应为真实 Team ID（非 0）。"""
        ws_url = f"ws://127.0.0.1:{self.backend_port}/ws/events.json"

        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.backend_base_url}/teams/list.json") as resp:
                assert resp.status == 200
                teams = (await resp.json())["teams"]
            team = next(t for t in teams if t["name"] == _TEAM)
            team_id = team["id"]

            async with session.get(f"{self.backend_base_url}/rooms/list.json") as resp:
                assert resp.status == 200
                rooms = (await resp.json())["rooms"]
            room_id = next(r["room_id"] for r in rooms if r["room_name"] == "general" and r["team_name"] == _TEAM)

            # 预置若干次 finish，确保调度链路能快速闭环，稳定产出 status 事件。
            finish_response = {"tool_calls": [{"name": "finish_chat_turn", "arguments": {}}]}
            for _ in range(4):
                self.set_mock_response(finish_response)

            async with session.ws_connect(ws_url) as ws:
                async with session.post(
                    f"{self.backend_base_url}/rooms/{room_id}/messages/send.json",
                    json={"content": "trigger status event"},
                ) as resp:
                    assert resp.status == 200

                matched = None
                async with asyncio.timeout(8):
                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            continue
                        data = json.loads(msg.data)
                        if data.get("event") != "agent_status":
                            continue
                        if data.get("agent_name") not in {"alice", "bob"}:
                            continue
                        matched = data
                        break

                assert matched is not None, "未收到 alice/bob 的 agent_status 事件"
                assert matched["team_id"] == team_id
                assert matched["team_id"] > 0
                assert "team_name" not in matched
