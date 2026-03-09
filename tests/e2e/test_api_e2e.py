"""端到端 API 测试——通过真实 HTTP 请求验证后端接口行为。"""
import aiohttp
import pytest


@pytest.mark.usefixtures("backend_process")
async def test_get_agents(http_client: aiohttp.ClientSession, backend_base: str):
    async with http_client.get(f"{backend_base}/agents") as resp:
        assert resp.status == 200
        data = await resp.json()
    assert "agents" in data
    assert len(data["agents"]) > 0
    agent = data["agents"][0]
    assert "name" in agent
    assert "model" in agent


@pytest.mark.usefixtures("backend_process")
async def test_get_rooms(http_client: aiohttp.ClientSession, backend_base: str):
    async with http_client.get(f"{backend_base}/rooms") as resp:
        assert resp.status == 200
        data = await resp.json()
    assert "rooms" in data
    assert len(data["rooms"]) > 0
    room = data["rooms"][0]
    assert "room_id" in room
    assert "room_name" in room
    assert "state" in room
    assert "members" in room


@pytest.mark.usefixtures("backend_process")
async def test_get_room_messages(http_client: aiohttp.ClientSession, backend_base: str):
    async with http_client.get(f"{backend_base}/rooms/general/messages") as resp:
        assert resp.status == 200
        data = await resp.json()
    assert "messages" in data
    assert "room_id" in data
    assert "room_name" in data
    # 初始话题应已被加入聊天记录
    assert len(data["messages"]) > 0
    msg = data["messages"][0]
    assert "sender" in msg
    assert "content" in msg
    assert "time" in msg


@pytest.mark.usefixtures("backend_process")
async def test_room_not_found(http_client: aiohttp.ClientSession, backend_base: str):
    async with http_client.get(f"{backend_base}/rooms/nonexistent/messages") as resp:
        assert resp.status == 404


@pytest.mark.usefixtures("backend_process")
async def test_ws_receives_message(ws_events: list):
    """验证 WebSocket 能接收到 event=message 类型的推送。"""
    assert len(ws_events) > 0, "未收到任何 event=message 的 WebSocket 推送"
    event = ws_events[0]
    assert event.get("event") == "message"
    assert "room_id" in event
    assert "sender" in event
    assert "content" in event
