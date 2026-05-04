import asyncio
import json
import os
import sys
import threading
import traceback

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
        """验证 POST 消息后 WebSocket 能收到 event=message 推送，且使用 gt_message 结构。"""
        collected = []
        ws_done = threading.Event()
        room_id_holder = []
        errors = []

        async def _collect():
            ws_url = f"ws://127.0.0.1:{self.backend_port}/ws/events.json"
            try:
                async with aiohttp.ClientSession() as session:
                    # 先获取 team_id
                    async with session.get(f"{self.backend_base_url}/teams/list.json") as resp:
                        assert resp.status == 200
                        teams = (await resp.json())["teams"]
                    team = next(t for t in teams if t["name"] == _TEAM)
                    team_id = team["id"]

                    async with session.get(f"{self.backend_base_url}/rooms/list.json?team_id={team_id}") as resp:
                        assert resp.status == 200
                        rooms = (await resp.json())["rooms"]
                    room_id = next(r["gt_room"]["id"] for r in rooms if r["gt_room"]["name"] == "general")
                    room_id_holder.append(room_id)
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
                errors.append(traceback.format_exc())
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

        assert not errors, errors[0]
        assert len(collected) > 0, "未收到任何 event=message 的 WebSocket 推送"
        event = collected[0]
        assert event.get("event") == "message"
        assert "gt_room" in event
        assert event["gt_room"]["id"] == room_id_holder[0]
        assert event["gt_room"]["team_id"] > 0
        assert event["gt_room"]["name"] == "general"
        assert "display_name" not in event["gt_room"]
        assert "i18n" in event["gt_room"]
        assert "gt_message" in event
        assert event["gt_message"]["sender_id"] == -1
        assert event["gt_message"]["content"] == "Testing WebSocket"
        assert event["gt_message"]["insert_immediately"] is False

    async def test_ws_agent_status_contains_real_team_id(self):
        """agent_status 事件中的 gt_agent.team_id 应为真实 Team ID（非 0）。"""
        ws_url = f"ws://127.0.0.1:{self.backend_port}/ws/events.json"

        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.backend_base_url}/teams/list.json") as resp:
                assert resp.status == 200
                teams = (await resp.json())["teams"]
            team = next(t for t in teams if t["name"] == _TEAM)
            team_id = team["id"]

            async with session.get(f"{self.backend_base_url}/rooms/list.json?team_id={team_id}") as resp:
                assert resp.status == 200
                rooms = (await resp.json())["rooms"]
            room_id = next(r["gt_room"]["id"] for r in rooms if r["gt_room"]["name"] == "general")

            # 预置若干次 finish，确保调度链路能快速闭环，稳定产出 status 事件。
            finish_response = {"tool_calls": [{"name": "finish_chat_turn", "arguments": {"confirm_no_need_talk": True}}]}
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
                        gt_agent = data.get("gt_agent") or {}
                        if gt_agent.get("name") not in {"alice", "bob"}:
                            continue
                        matched = data
                        break

                assert matched is not None, "未收到 alice/bob 的 agent_status 事件"
                assert matched["gt_agent"]["team_id"] == team_id
                assert matched["gt_agent"]["team_id"] > 0
                assert "display_name" not in matched["gt_agent"]
                assert "i18n" in matched["gt_agent"]
                assert "team_name" not in matched

    async def test_ws_room_status_contains_current_turn_agent_id(self):
        """room_status 事件应携带 current_turn_agent_id，供前端自行匹配成员。"""
        ws_url = f"ws://127.0.0.1:{self.backend_port}/ws/events.json"

        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.backend_base_url}/teams/list.json") as resp:
                assert resp.status == 200
                teams = (await resp.json())["teams"]
            team = next(t for t in teams if t["name"] == _TEAM)
            team_id = team["id"]

            async with session.get(f"{self.backend_base_url}/rooms/list.json?team_id={team_id}") as resp:
                assert resp.status == 200
                rooms = (await resp.json())["rooms"]
            room_id = next(r["gt_room"]["id"] for r in rooms if r["gt_room"]["name"] == "general")

            finish_response = {"tool_calls": [{"name": "finish_chat_turn", "arguments": {"confirm_no_need_talk": True}}]}
            for _ in range(4):
                self.set_mock_response(finish_response)

            async with session.ws_connect(ws_url) as ws:
                async with session.post(
                    f"{self.backend_base_url}/rooms/{room_id}/messages/send.json",
                    json={"content": "trigger room status event"},
                ) as resp:
                    assert resp.status == 200

                matched = None
                async with asyncio.timeout(8):
                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            continue
                        data = json.loads(msg.data)
                        if data.get("event") != "room_status":
                            continue
                        current_turn_agent_id = data.get("current_turn_agent_id")
                        if not isinstance(current_turn_agent_id, int) or current_turn_agent_id <= 0:
                            continue
                        matched = data
                        break

                assert matched is not None, "未收到包含 current_turn_agent_id 的 room_status 事件"
                assert matched["gt_room"]["team_id"] == team_id
                assert matched["current_turn_agent_id"] > 0
