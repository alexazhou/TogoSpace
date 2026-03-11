import asyncio
import logging

import aiohttp
from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Header, ListView, Input, Static

from api_client import ApiClient, RoomInfo, WsEvent
from widgets import MessageView, RoomPanel, StatusBar

log = logging.getLogger("tui.app")


def _make_preview(sender: str, content: str) -> str:
    """生成预览文字（换行替换为空格），截断由 PreviewLabel 动态处理。"""
    return f"{sender}: {content.replace(chr(10), ' ')}"


class WatcherApp(App):
    TITLE = "Team Agent TUI"
    CSS_PATH = "app.tcss"

    BINDINGS = [
        ("ctrl+q", "quit", "退出"),
        ("ctrl+c", "hint_quit", ""),
        ("up", "prev_room", "上一个房间"),
        ("down", "next_room", "下一个房间"),
        ("enter", "select_room", "切换到当前房间"),
        ("i", "focus_input", "进入输入模式"),
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
        self._agent_refresh_pending: bool = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main-horizontal"):
            yield RoomPanel()
            with Vertical(id="right-panel"):
                yield MessageView()
                with Vertical(id="chat-input-container"):
                    yield Input(placeholder="在此输入消息...", id="chat-input")
                yield StatusBar()

    async def _on_mount(self) -> None:
        log.info("on_mount 触发")
        await self._refresh_full_ui(is_initial=True)
        log.info("on_mount 完成，启动 ws loop")
        self._start_ws_loop()

    async def _fetch_all_previews(self, rooms: list[RoomInfo]) -> dict[str, str]:
        """并发拉取各房间最后一条消息作为预览。"""
        previews: dict[str, str] = {}

        async def _fetch(room: RoomInfo) -> None:
            try:
                msgs = await self._api.get_room_messages(room.room_id)
                if msgs:
                    last = msgs[-1]
                    previews[room.room_id] = _make_preview(last.sender, last.content)
            except Exception:
                pass

        await asyncio.gather(*[_fetch(r) for r in rooms])
        return previews

    async def _refresh_full_ui(self, is_initial: bool = False) -> None:
        """刷新房间列表、Agent 列表及 UI 状态。"""
        status_bar = self.query_one(StatusBar)
        message_view = self.query_one(MessageView)
        room_panel = self.query_one(RoomPanel)

        try:
            agents, rooms = await asyncio.gather(
                self._api.get_agents(),
                self._api.get_rooms(),
            )
            self._agent_order = [a.name for a in agents]
            self._rooms = rooms

            previews = await self._fetch_all_previews(rooms)
            await room_panel.load(rooms, agents, previews)

            # 初始加载或重连后恢复选中的房间
            target_room_id = self._current_room_id or (rooms[0].room_id if rooms else None)
            if target_room_id:
                await self._select_room(target_room_id, force_reload=True)

            if not is_initial:
                status_bar.set_connected()
        except aiohttp.ClientError:
            status_bar.set_disconnected()
            if is_initial:
                await message_view.append_message(
                    "system", "无法连接到后端服务，请检查服务是否已启动。", []
                )

    async def _select_room(self, room_id: str, force_reload: bool = False) -> None:
        if not force_reload and room_id == self._current_room_id:
            return

        message_view = self.query_one(MessageView)
        status_bar = self.query_one(StatusBar)
        room_panel = self.query_one(RoomPanel)
        input_container = self.query_one("#chat-input-container")

        try:
            messages = await self._api.get_room_messages(room_id)
            await message_view.load_messages(messages, self._agent_order)
            room_panel.mark_selected(room_id)
            room_panel.update_unread_count(room_id, 0)
            self._unread[room_id] = 0
            self._current_room_id = room_id
            self._current_msg_count = len(messages)
            status_bar.update_count(self._current_msg_count)

            # 查找房间信息以确定类型
            current_room = next((r for r in self._rooms if r.room_id == room_id), None)
            if current_room and current_room.room_type == "private":
                input_container.add_class("active")
            else:
                input_container.remove_class("active")
                self.query_one("#chat-input").value = ""

            for i, r in enumerate(self._rooms):
                if r.room_id == room_id:
                    self._room_cursor = i
                    break
        except ValueError:
            await message_view.append_message("system", f"房间不存在: {room_id}", [])
        except aiohttp.ClientError:
            await message_view.append_message("system", "加载消息失败，请检查网络连接。", [])

    @work(exclusive=True, group="ws")
    async def _start_ws_loop(self) -> None:
        status_bar = self.query_one(StatusBar)
        while True:
            log.info("ws: 开始连接")

            def _on_connected() -> None:
                log.info("ws: 连接成功，刷新房间/Agent/消息数据")
                self.call_later(self._refresh_full_ui)

            try:
                async for event in self._api.ws_events(on_connected=_on_connected):
                    log.debug("ws: 收到事件 room=%s sender=%s", event.room_id, event.sender)
                    self._on_ws_event(event)
                log.info("ws: 连接正常关闭（async for 退出）")
            except asyncio.CancelledError:
                log.info("ws: worker 被取消，退出循环")
                raise
            except Exception as e:
                log.warning("ws: 连接异常断开: %s: %s", type(e).__name__, e)

            log.info("ws: 切换为已断开，3 秒后重连")
            for remaining in range(3, 0, -1):
                status_bar.set_disconnected(remaining)
                await asyncio.sleep(1)
            status_bar.set_reconnecting()

    def _on_ws_event(self, event: WsEvent) -> None:
        message_view = self.query_one(MessageView)
        status_bar = self.query_one(StatusBar)
        room_panel = self.query_one(RoomPanel)

        if event.event == "agent_status":
            log.debug("ws: 收到 Agent 状态变更 agent=%s status=%s", event.agent_name, event.status)
            self._schedule_agent_refresh()
            return

        preview = _make_preview(event.sender, event.content)
        self.call_later(room_panel.update_preview, event.room_id, preview)
        self._schedule_agent_refresh()

        if event.room_id == self._current_room_id:
            self._current_msg_count += 1
            self.call_later(
                message_view.append_message, event.sender, event.content, self._agent_order
            )
            self.call_later(status_bar.update_count, self._current_msg_count)
        else:
            self._unread[event.room_id] = self._unread.get(event.room_id, 0) + 1
            self.call_later(room_panel.update_unread_count, event.room_id, self._unread[event.room_id])

    def _schedule_agent_refresh(self) -> None:
        """节流：已有刷新任务在排队时跳过本次。"""
        if not self._agent_refresh_pending:
            self._agent_refresh_pending = True
            self._refresh_agent_status()

    @work(exclusive=True, group="agent_refresh")
    async def _refresh_agent_status(self) -> None:
        self._agent_refresh_pending = False
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

    @on(Input.Submitted, "#chat-input")
    async def on_input_submitted(self, event: Input.Submitted) -> None:
        content = event.value.strip()
        if not content or not self._current_room_id:
            return

        success = await self._api.post_room_message(self._current_room_id, content)
        if success:
            self.query_one("#chat-input").value = ""
        else:
            self.notify("消息发送失败", severity="error")

    def action_focus_input(self) -> None:
        current_room = next((r for r in self._rooms if r.room_id == self._current_room_id), None)
        if current_room and current_room.room_type == "private":
            self.query_one("#chat-input").focus()

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

    def on_exception(self, error: Exception) -> None:
        log.exception("未捕获异常导致 app 退出: %s", error)

    async def _on_unmount(self) -> None:
        log.info("app unmount")
        await self._api.close()
