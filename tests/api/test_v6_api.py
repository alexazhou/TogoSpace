import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.request
from typing import List

import aiohttp
import pytest
import pytest_asyncio
from constants import RoomType, SpecialAgent

TEAM = "v6test"


def _find_free_port() -> int:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def v6_backend(tmp_path_factory, mock_llm_server):
    """启动一个包含 private 房间的后端进程。"""
    port = _find_free_port()
    tmp_dir = str(tmp_path_factory.mktemp("v6_e2e"))

    # 准备 config 目录结构
    config_dir = os.path.join(tmp_dir, "config")
    agents_dir = os.path.join(config_dir, "agents")
    teams_dir = os.path.join(config_dir, "teams")
    os.makedirs(agents_dir, exist_ok=True)
    os.makedirs(teams_dir, exist_ok=True)

    # Agent 定义
    alice_agent = {"name": "alice", "system_prompt": "Mock Alice Prompt", "model": "mock-model"}
    with open(os.path.join(agents_dir, "alice.json"), "w", encoding="utf-8") as f:
        json.dump(alice_agent, f, ensure_ascii=False)

    # Team 定义
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

    # LLM 配置
    llm_cfg = {
        "llm_services": [
            {
                "name": "mock",
                "base_url": f"http://127.0.0.1:{mock_llm_server.port}/v1/chat/completions",
                "api_key": "mock-api-key",
                "type": "openai-compatible",
            }
        ],
        "active_llm_service": "mock",
    }
    llm_path = os.path.join(tmp_dir, "llm_v6.json")
    with open(llm_path, "w", encoding="utf-8") as f:
        json.dump(llm_cfg, f)

    src_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../src"))
    env = os.environ.copy()
    env["PYTHONPATH"] = src_dir

    proc = subprocess.Popen(
        [
            sys.executable,
            os.path.join(src_dir, "main.py"),
            "--config-dir", config_dir,
            "--llm-config", llm_path,
            "--port", str(port),
        ],
        cwd=src_dir,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    # 等待就绪
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/agents", timeout=1) as resp:
                if resp.status == 200:
                    break
        except Exception:
            pass
        time.sleep(0.5)
    else:
        proc.terminate()
        raise RuntimeError("V6 后端启动超时")

    yield base_url

    proc.terminate()
    proc.wait()


@pytest_asyncio.fixture
async def client():
    async with aiohttp.ClientSession() as session:
        yield session


@pytest.mark.asyncio
async def test_room_types_in_list(client: aiohttp.ClientSession, v6_backend: str):
    """验证 GET /rooms 是否正确返回 room_type 和 team_name 字段。"""
    async with client.get(f"{v6_backend}/rooms") as resp:
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


@pytest.mark.asyncio
async def test_post_message_to_private_room(client: aiohttp.ClientSession, v6_backend: str):
    """验证向 private 房间发送消息的功能及其触发的 Agent 响应。"""
    room_id = f"alice_private@{TEAM}"

    # 1. 发送消息
    payload = {"content": "Hello Alice, I am the operator."}
    async with client.post(f"{v6_backend}/rooms/{room_id}/messages", json=payload) as resp:
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "ok"

    # 2. 检查消息列表，验证 Operator 消息已存入
    async with client.get(f"{v6_backend}/rooms/{room_id}/messages") as resp:
        assert resp.status == 200
        data = await resp.json()
        messages = data["messages"]
        # initial_topic (system) + operator_msg
        assert len(messages) >= 2
        assert messages[1]["sender"] == SpecialAgent.OPERATOR.value
        assert messages[1]["content"] == payload["content"]

    # 3. 等待并验证 Agent (Alice) 的响应
    max_wait = 15
    start_time = time.time()
    messages = []
    while time.time() - start_time < max_wait:
        async with client.get(f"{v6_backend}/rooms/{room_id}/messages") as resp:
            assert resp.status == 200
            data = await resp.json()
            messages = data["messages"]
            if any(m["sender"] == "alice" for m in messages):
                break
        await asyncio.sleep(1)
    else:
        print(f"\nDebug - Final messages in {room_id}:")
        for m in messages:
            print(f"  {m['sender']}: {m['content'][:50]}...")
        pytest.fail("Agent Alice 未能在限时内回复 Operator")

    # 验证 Alice 的回复
    alice_msg = next(m for m in messages if m["sender"] == "alice")
    assert len(alice_msg["content"]) > 0
