import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import AsyncGenerator

import aiohttp

log = logging.getLogger("tui.api")


@dataclass
class AgentInfo:
    name: str
    model: str
    team_name: str
    status: str = "idle"  # "active" | "idle"


@dataclass
class TeamInfo:
    id: int
    name: str


@dataclass
class RoomInfo:
    room_id: int
    room_key: str
    room_name: str
    team_name: str
    room_type: str
    state: str
    members: list[str]


@dataclass
class MessageInfo:
    sender: str
    content: str
    time: datetime


@dataclass
class WsEvent:
    event: str
    room_id: int | None = None
    room_key: str | None = None
    room_name: str | None = None
    team_name: str | None = None
    sender: str | None = None
    content: str | None = None
    time: datetime | None = None
    agent_name: str | None = None
    status: str | None = None


class ApiClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._session: aiohttp.ClientSession | None = None

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def get_agents(self, team_name: str | None = None) -> list[AgentInfo]:
        session = self._get_session()
        params: dict[str, str] | None = None
        if team_name:
            params = {"team_name": team_name}
        async with session.get(f"{self._base_url}/agents/list.json", params=params) as resp:
            resp.raise_for_status()
            data = await resp.json()
        return [AgentInfo(name=a["name"], model=a["model"], team_name=a.get("team_name", ""), status=a.get("status", "idle")) for a in data["agents"]]

    async def get_teams(self) -> list[TeamInfo]:
        session = self._get_session()
        async with session.get(f"{self._base_url}/teams/list.json") as resp:
            resp.raise_for_status()
            data = await resp.json()
        return [TeamInfo(id=t["id"], name=t["name"]) for t in data.get("teams", [])]

    async def get_rooms(self) -> list[RoomInfo]:
        session = self._get_session()
        async with session.get(f"{self._base_url}/rooms/list.json") as resp:
            resp.raise_for_status()
            data = await resp.json()
        return [
            RoomInfo(
                room_id=r["room_id"],
                room_key=r["room_key"],
                room_name=r["room_name"],
                team_name=r.get("team_name", ""),
                room_type=(r.get("room_type", "group") or "group").lower(),
                state=r["state"],
                members=r["members"],
            )
            for r in data["rooms"]
        ]

    async def get_room_members(self, team_id: int, room_id: int) -> list[str]:
        session = self._get_session()
        async with session.get(f"{self._base_url}/teams/{team_id}/rooms/{room_id}/members/list.json") as resp:
            if resp.status == 404:
                raise ValueError(f"Room members not found: team_id={team_id}, room_id={room_id}")
            resp.raise_for_status()
            data = await resp.json()
        members = data.get("members", [])
        return [str(m) for m in members]

    async def get_room_messages(self, room_id: int) -> list[MessageInfo]:
        session = self._get_session()
        async with session.get(f"{self._base_url}/rooms/{room_id}/messages/list.json") as resp:
            if resp.status == 404:
                raise ValueError(f"Room not found: {room_id}")
            resp.raise_for_status()
            data = await resp.json()
        return [
            MessageInfo(
                sender=m["sender"],
                content=m["content"],
                time=datetime.fromisoformat(m["time"]),
            )
            for m in data["messages"]
        ]

    async def post_room_message(self, room_id: int, content: str) -> bool:
        session = self._get_session()
        async with session.post(f"{self._base_url}/rooms/{room_id}/messages/send.json", json={"content": content}) as resp:
            return resp.status == 200

    async def ws_events(self, on_connected=None) -> AsyncGenerator[WsEvent, None]:
        ws_url = self._base_url.replace("http://", "ws://").replace("https://", "wss://")
        session = self._get_session()
        try:
            async with session.ws_connect(f"{ws_url}/ws/events.json", heartbeat=5) as ws:
                log.info("ws_connect 握手成功: %s", f"{ws_url}/ws/events")
                if on_connected:
                    on_connected()
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        try:
                            data = json.loads(msg.data)
                            event_type = data.get("event", "message")
                            if event_type == "message":
                                yield WsEvent(
                                    event=event_type,
                                    room_id=data["room_id"],
                                    room_key=data.get("room_key"),
                                    room_name=data["room_name"],
                                    team_name=data.get("team_name", ""),
                                    sender=data["sender"],
                                    content=data["content"],
                                    time=datetime.fromisoformat(data["time"]),
                                )
                            elif event_type == "agent_status":
                                yield WsEvent(
                                    event=event_type,
                                    agent_name=data["agent_name"],
                                    team_name=data.get("team_name", ""),
                                    status=data["status"],
                                )
                            else:
                                log.warning("ws: 收到未知事件类型: %s", event_type)
                        except (KeyError, ValueError) as e:
                            log.warning("ws: 解析事件失败: %s, data=%s", e, msg.data)
                            continue
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        log.info("ws: 收到关闭/错误帧, type=%s", msg.type)
                        break
                log.info("ws: async for 结束（服务端关闭连接）")
        except Exception as e:
            log.warning("ws_connect 异常: %s: %s", type(e).__name__, e)
            return

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
