import asyncio
import json
import os
import shutil
import tempfile
import time

import aiohttp
import pytest
from constants import RoomType, SpecialAgent

from ..base import ServiceTestCase

TEAM = "v6test"


class TestV6Api(ServiceTestCase):
    requires_backend = True
    requires_mock_llm = True

    _tmp_dir: str = None

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
                    "name": "alice_private",
                    "type": RoomType.PRIVATE.value,
                    "members": [SpecialAgent.OPERATOR.value, "alice"],
                    "initial_topic": "v6 private test",
                    "max_turns": 10,
                },
                {
                    "name": "public_group",
                    "type": RoomType.GROUP.value,
                    "members": ["alice"],
                    "initial_topic": "v6 group test",
                    "max_turns": 10,
                },
            ],
            "max_function_calls": 5,
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
        llm_path = os.path.join(cls._tmp_dir, "llm_v6.json")
        with open(llm_path, "w", encoding="utf-8") as f:
            json.dump(llm_cfg, f)

        cls._backend_config_dir = config_dir
        cls._backend_llm_config = llm_path

    @classmethod
    def teardown_class(cls):
        super().teardown_class()
        if cls._tmp_dir:
            shutil.rmtree(cls._tmp_dir, ignore_errors=True)
            cls._tmp_dir = None

    async def test_room_types_in_list(self):
        """验证 GET /rooms 是否正确返回 room_type 和 team_name 字段。"""
        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/rooms") as resp:
                assert resp.status == 200
                data = await resp.json()

        rooms = data["rooms"]
        assert len(rooms) == 2

        private_room = next(r for r in rooms if r["room_name"] == "alice_private")
        assert private_room["room_type"] == RoomType.PRIVATE.value
        assert private_room["team_name"] == TEAM
        assert SpecialAgent.OPERATOR.value in private_room["members"]

        group_room = next(r for r in rooms if r["room_name"] == "public_group")
        assert group_room["room_type"] == RoomType.GROUP.value
        assert group_room["team_name"] == TEAM
        assert SpecialAgent.OPERATOR.value not in group_room["members"]

    async def test_post_message_to_private_room(self):
        """验证向 private 房间发送消息的功能及其触发的 Agent 响应。"""
        room_id = f"alice_private@{TEAM}"

        async with aiohttp.ClientSession() as client:
            # 1. 发送消息
            payload = {"content": "Hello Alice, I am the operator."}
            async with client.post(
                f"{self.backend_base_url}/rooms/{room_id}/messages", json=payload
            ) as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["status"] == "ok"

            # 2. 检查消息列表，验证 Operator 消息已存入
            async with client.get(f"{self.backend_base_url}/rooms/{room_id}/messages") as resp:
                assert resp.status == 200
                data = await resp.json()
                messages = data["messages"]
                assert len(messages) >= 2
                assert messages[1]["sender"] == SpecialAgent.OPERATOR.value
                assert messages[1]["content"] == payload["content"]

        # 3. 等待并验证 Agent (Alice) 的响应
        max_wait = 15
        start_time = time.time()
        messages = []
        while time.time() - start_time < max_wait:
            async with aiohttp.ClientSession() as client:
                async with client.get(
                    f"{self.backend_base_url}/rooms/{room_id}/messages"
                ) as resp:
                    assert resp.status == 200
                    data = await resp.json()
                    messages = data["messages"]
                    if any(m["sender"] == "alice" for m in messages):
                        break
            await asyncio.sleep(1)
        else:
            pytest.fail("Agent Alice 未能在限时内回复 Operator")

        alice_msg = next(m for m in messages if m["sender"] == "alice")
        assert len(alice_msg["content"]) > 0
