import json
from dataclasses import dataclass
from datetime import datetime
from typing import AsyncGenerator

import aiohttp


@dataclass
class AgentInfo:
    name: str
    model: str


@dataclass
class RoomInfo:
    room_id: str
    room_name: str
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
    room_id: str
    room_name: str
    sender: str
    content: str
    time: datetime


class ApiClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._session: aiohttp.ClientSession | None = None

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def get_agents(self) -> list[AgentInfo]:
        session = self._get_session()
        async with session.get(f"{self._base_url}/agents") as resp:
            resp.raise_for_status()
            data = await resp.json()
        return [AgentInfo(name=a["name"], model=a["model"]) for a in data["agents"]]

    async def get_rooms(self) -> list[RoomInfo]:
        session = self._get_session()
        async with session.get(f"{self._base_url}/rooms") as resp:
            resp.raise_for_status()
            data = await resp.json()
        return [
            RoomInfo(
                room_id=r["room_id"],
                room_name=r["room_name"],
                state=r["state"],
                members=r["members"],
            )
            for r in data["rooms"]
        ]

    async def get_room_messages(self, room_id: str) -> list[MessageInfo]:
        session = self._get_session()
        async with session.get(f"{self._base_url}/rooms/{room_id}/messages") as resp:
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

    async def ws_events(self) -> AsyncGenerator[WsEvent, None]:
        ws_url = self._base_url.replace("http://", "ws://").replace("https://", "wss://")
        session = self._get_session()
        try:
            async with session.ws_connect(f"{ws_url}/ws/events") as ws:
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        try:
                            data = json.loads(msg.data)
                            yield WsEvent(
                                event=data.get("event", "message"),
                                room_id=data["room_id"],
                                room_name=data["room_name"],
                                sender=data["sender"],
                                content=data["content"],
                                time=datetime.fromisoformat(data["time"]),
                            )
                        except (KeyError, ValueError):
                            continue
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
        except Exception:
            return

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
