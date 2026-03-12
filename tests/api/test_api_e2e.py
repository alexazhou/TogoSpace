"""端到端 API 测试——通过真实 HTTP 请求验证后端接口行为。"""
import asyncio
import json
import os
import shutil
import tempfile
import threading

import aiohttp

from ..base import ServiceTestCase

TEAM = "e2e"


class TestApiE2e(ServiceTestCase):
    requires_backend = True
    requires_mock_llm = True

    _tmp_dir: str = None
    ws_events: list = None

    @classmethod
    def _setup_pre_backend(cls):
        cls._tmp_dir = tempfile.mkdtemp()
        config_dir = os.path.join(cls._tmp_dir, "config")
        agents_dir = os.path.join(config_dir, "agents")
        teams_dir = os.path.join(config_dir, "teams")
        os.makedirs(agents_dir)
        os.makedirs(teams_dir)

        alice_agent = {"name": "alice", "system_prompt": "Mock Alice Prompt", "model": "mock-model"}
        with open(os.path.join(agents_dir, "alice.json"), "w", encoding="utf-8") as f:
            json.dump(alice_agent, f, ensure_ascii=False)

        team = {
            "name": TEAM,
            "groups": [
                {
                    "name": "general",
                    "type": "group",
                    "members": ["alice"],
                    "initial_topic": "e2e 测试话题",
                    "max_turns": 50,
                }
            ],
            "max_function_calls": 2,
        }
        with open(os.path.join(teams_dir, f"{TEAM}.json"), "w", encoding="utf-8") as f:
            json.dump(team, f, ensure_ascii=False)

        llm_cfg = {
            "llm_services": [
                {
                    "name": "mock",
                    "base_url": f"http://127.0.0.1:{cls.mock_llm_port}/v1/chat/completions",
                    "api_key": "mock-api-key",
                    "type": "openai-compatible",
                }
            ],
            "active_llm_service": "mock",
        }
        llm_path = os.path.join(cls._tmp_dir, "llm_e2e.json")
        with open(llm_path, "w", encoding="utf-8") as f:
            json.dump(llm_cfg, f)

        cls._backend_config_dir = config_dir
        cls._backend_llm_config = llm_path

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

    @classmethod
    def teardown_class(cls):
        super().teardown_class()
        if cls._tmp_dir:
            shutil.rmtree(cls._tmp_dir, ignore_errors=True)
            cls._tmp_dir = None

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
