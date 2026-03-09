import asyncio

import aiohttp
from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Header, ListView

from api_client import ApiClient, RoomInfo, WsEvent
from widgets import MessageView, RoomPanel, StatusBar


def _make_preview(sender: str, content: str) -> str:
    """生成预览文字（换行替换为空格），截断由 PreviewLabel 动态处理。"""
    return f"{sender}: {content.replace(chr(10), ' ')}"


class WatcherApp(App):
    TITLE = "Team Agent TUI"
    CSS = """
    Screen {
        background: #0d1117;
    }

    #main-horizontal {
        height: 1fr;
    }

    RoomPanel {
        width: 25%;
        min-width: 20;
        background: #161b22;
        border-right: solid #30363d;
    }

    RoomPanel .panel-title {
        width: 100%;
        text-align: center;
        background: #21262d;
        color: #8b949e;
        padding: 0 1;
        text-style: bold;
    }

    RoomPanel ListView {
        background: #161b22;
        border: none;
    }

    #room-list {
        height: 2fr;
    }

    #agent-list {
        height: 1fr;
        border-top: solid #30363d;
    }

    RoomPanel ListItem {
        padding: 0;
        margin-bottom: 1;
    }

    .room-card {
        padding: 0 1;
        height: auto;
        width: 100%;
    }

    .room-card-header {
        width: 100%;
        height: auto;
    }

    .room-card-name {
        text-style: bold;
        color: #c9d1d9;
        width: 1fr;
    }

    .room-card-members {
        width: auto;
        text-align: right;
        color: #8b949e;
    }

    .room-card-preview {
        color: #6e7681;
        width: 100%;
        overflow: hidden;
    }

    .selected-room {
        background: #112240;
    }

    #right-panel {
        width: 1fr;
    }

    MessageView {
        height: 1fr;
        padding: 1 2;
        background: #0d1117;
        overflow-x: hidden;
    }

    MessageBubble {
        margin-bottom: 1;
        width: 100%;
        height: auto;
    }

    .bubble-row {
        width: 100%;
        height: auto;
    }

    .bubble-spacer {
        width: 40%;
        height: auto;
    }

    .bubble-inner {
        width: auto;
        height: auto;
    }

    .sender {
        text-style: bold;
        width: 100%;
        height: auto;
    }

    .sender-left {
        text-align: left;
    }

    .sender-right {
        text-align: right;
    }

    .bubble {
        padding: 0 1;
        width: 100%;
        height: auto;
    }

    .bubble-left {
        background: #112240;
        color: #c8ccd0;
        text-align: left;
    }

    .bubble-right {
        background: #221a0e;
        color: #c8ccd0;
        text-align: left;
    }

    .bubble-system {
        width: 100%;
        text-align: center;
        color: #8b949e;
    }

    .agent-card {
        width: 100%;
        height: auto;
        padding: 0 1;
    }

    .agent-name {
        width: 1fr;
    }

    .agent-status {
        width: auto;
        text-align: right;
    }

    StatusBar {
        height: 1;
        background: #161b22;
        padding: 0 2;
        color: #6e7681;
        dock: bottom;
    }
    """

    BINDINGS = [
        ("ctrl+q", "quit", "退出"),
        ("ctrl+c", "hint_quit", ""),
        ("up", "prev_room", "上一个房间"),
        ("down", "next_room", "下一个房间"),
        ("enter", "select_room", "切换到当前房间"),
    ]

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self._api = ApiClient(base_url)
        self._agent_order: list[str] = []
        self._unread: dict[str, int] = {}
        self._rooms: list[RoomInfo] = []
        self._room_cursor: int = 0
        self._current_room_id: str | None = None
        self._current_msg_count: int = 0

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main-horizontal"):
            yield RoomPanel()
            with Vertical(id="right-panel"):
                yield MessageView()
                yield StatusBar("○ 已断开")

    async def on_mount(self) -> None:
        status_bar = self.query_one(StatusBar)
        message_view = self.query_one(MessageView)
        room_panel = self.query_one(RoomPanel)

        try:
            agents = await self._api.get_agents()
            rooms = await self._api.get_rooms()
            self._agent_order = [a.name for a in agents]
            self._rooms = rooms

            # 并发拉取各房间最后一条消息作为预览
            last_previews: dict[str, str] = {}
            async def _fetch_preview(room: RoomInfo) -> None:
                try:
                    msgs = await self._api.get_room_messages(room.room_id)
                    if msgs:
                        last = msgs[-1]
                        last_previews[room.room_id] = _make_preview(last.sender, last.content)
                except Exception:
                    pass
            await asyncio.gather(*[_fetch_preview(r) for r in rooms])

            room_panel.load(rooms, agents, last_previews)
            if rooms:
                await self._select_room(rooms[0].room_id)
        except aiohttp.ClientError:
            status_bar.set_disconnected()
            await message_view.append_message(
                "system", "无法连接到后端服务，请检查服务是否已启动。", []
            )

        self._start_ws_loop()

    async def _select_room(self, room_id: str) -> None:
        message_view = self.query_one(MessageView)
        status_bar = self.query_one(StatusBar)
        room_panel = self.query_one(RoomPanel)

        try:
            messages = await self._api.get_room_messages(room_id)
            await message_view.load_messages(messages, self._agent_order)
            room_panel.mark_selected(room_id)
            room_panel.clear_unread(room_id)
            self._unread[room_id] = 0
            self._current_room_id = room_id
            self._current_msg_count = len(messages)
            status_bar.update_count(self._current_msg_count)
            # Update cursor index
            for i, r in enumerate(self._rooms):
                if r.room_id == room_id:
                    self._room_cursor = i
                    break
        except ValueError:
            await message_view.append_message("system", f"房间不存在: {room_id}", [])
        except aiohttp.ClientError:
            await message_view.append_message("system", "加载消息失败，请检查网络连接。", [])

    @work(exclusive=True)
    async def _start_ws_loop(self) -> None:
        status_bar = self.query_one(StatusBar)
        while True:
            try:
                status_bar.set_connected()
                async for event in self._api.ws_events():
                    self._on_ws_event(event)
            except Exception:
                pass
            status_bar.set_disconnected()
            await asyncio.sleep(3)
            status_bar.set_reconnecting()

    def _on_ws_event(self, event: WsEvent) -> None:
        message_view = self.query_one(MessageView)
        status_bar = self.query_one(StatusBar)
        room_panel = self.query_one(RoomPanel)

        preview = _make_preview(event.sender, event.content)
        self.call_later(room_panel.update_preview, event.room_id, preview)
        self._refresh_agent_status()

        if event.room_id == self._current_room_id:
            self._current_msg_count += 1
            self.call_later(
                message_view.append_message, event.sender, event.content, self._agent_order
            )
            self.call_later(status_bar.update_count, self._current_msg_count)
        else:
            self._unread[event.room_id] = self._unread.get(event.room_id, 0) + 1
            self.call_later(room_panel.set_unread, event.room_id, self._unread[event.room_id])

    @work(exclusive=False)
    async def _refresh_agent_status(self) -> None:
        try:
            agents = await self._api.get_agents()
            room_panel = self.query_one(RoomPanel)
            self.call_later(room_panel.update_agent_status, agents)
        except Exception:
            pass

    @on(ListView.Selected, "#room-list")
    async def on_room_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if item.id and item.id.startswith("room-"):
            room_id = item.id[len("room-"):]
            await self._select_room(room_id)

    async def action_prev_room(self) -> None:
        if not self._rooms:
            return
        self._room_cursor = (self._room_cursor - 1) % len(self._rooms)
        await self._select_room(self._rooms[self._room_cursor].room_id)

    async def action_next_room(self) -> None:
        if not self._rooms:
            return
        self._room_cursor = (self._room_cursor + 1) % len(self._rooms)
        await self._select_room(self._rooms[self._room_cursor].room_id)

    async def action_select_room(self) -> None:
        if not self._rooms:
            return
        await self._select_room(self._rooms[self._room_cursor].room_id)

    def action_hint_quit(self) -> None:
        self.notify(
            "按 [bold]Ctrl+Q[/bold] 退出程序",
            title="想退出吗？",
            severity="information",
            timeout=3,
        )

    async def on_unmount(self) -> None:
        await self._api.close()
