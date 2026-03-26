import asyncio
import logging
import sys

import aiohttp
from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Header, ListView, Input, Static

from api_client import ApiClient, RoomInfo, AgentInfo, WsEvent
from widgets import MessageView, RoomPanel, StatusBar

log = logging.getLogger("tui.app")


def _make_preview(sender: str, content: str) -> str:
    """生成预览文字（换行替换为空格），截断由 PreviewLabel 动态处理。"""
    return f"{sender}: {content.replace(chr(10), ' ')}"


class WatcherApp(App):
    TITLE = "Team Agent TUI"
    SUB_TITLE = ""
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
        self._agents: list[AgentInfo] = []  # 本地 Agent 状态缓存
        self._team_ids_by_name: dict[str, int] = {}
        self._room_members_by_key: dict[str, list[str]] = {}
        self._room_cursor: int = 0
        self._current_room_key: str | None = None
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
                yield Static("当前为观察模式", id="chat-input-hint")
                yield StatusBar()

    async def on_ready(self) -> None:
        log.info("on_ready 触发, 设置竖线光标")
        # 设置光标为闪烁竖线 (5: Blinking Bar)
        sys.stdout.write("\x1b[5 q")
        sys.stdout.flush()

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
                    previews[room.room_key] = _make_preview(last.sender, last.content)
            except Exception:
                pass

        await asyncio.gather(*[_fetch(r) for r in rooms])
        return previews

    async def _fetch_agents_for_rooms(self, rooms: list[RoomInfo]) -> list[AgentInfo]:
        """按房间所属 team 拉取运行态 Agent 列表，确保 team_name/status 可用。"""
        if not rooms:
            return await self._api.get_agents()

        team_names = sorted({r.team_name for r in rooms if r.team_name})
        if not team_names:
            return await self._api.get_agents()

        fetched = await asyncio.gather(*[self._api.get_agents(team_name=t) for t in team_names])
        merged: list[AgentInfo] = []
        seen: set[tuple[str, str]] = set()
        for team_agents in fetched:
            for agent in team_agents:
                key = (agent.team_name, agent.name)
                if key in seen:
                    continue
                seen.add(key)
                merged.append(agent)
        return merged

    async def _refresh_full_ui(self, is_initial: bool = False) -> None:
        """刷新房间列表、团队成员列表及 UI 状态。"""
        status_bar = self.query_one(StatusBar)
        message_view = self.query_one(MessageView)
        room_panel = self.query_one(RoomPanel)

        try:
            teams, rooms = await asyncio.gather(
                self._api.get_teams(),
                self._api.get_rooms(),
            )
            self._team_ids_by_name = {t.name: t.id for t in teams}
            agents = await self._fetch_agents_for_rooms(rooms)
            self._agents = agents  # 更新本地缓存
            self._agent_order = [a.name for a in agents]
            self._rooms = rooms
            self._room_members_by_key = {r.room_key: list(r.members) for r in rooms}

            previews = await self._fetch_all_previews(rooms)
            await room_panel.load(rooms, agents, previews)

            # 初始加载或重连后恢复选中的房间
            target_room_key = self._current_room_key or (rooms[0].room_key if rooms else None)
            if target_room_key:
                await self._select_room(target_room_key, force_reload=True)
            else:
                await room_panel.update_team_members(None, self._agents)

            if not is_initial:
                status_bar.set_connected()
        except aiohttp.ClientError:
            status_bar.set_disconnected()
            if is_initial:
                await message_view.append_message(
                    "system", "无法连接到后端服务，请检查服务是否已启动。", []
                )

    async def _select_room(self, room_key: str, force_reload: bool = False) -> None:
        if not force_reload and room_key == self._current_room_key:
            return

        message_view = self.query_one(MessageView)
        status_bar = self.query_one(StatusBar)
        room_panel = self.query_one(RoomPanel)
        input_container = self.query_one("#chat-input-container")
        hint_label = self.query_one("#chat-input-hint")

        try:
            # 根据 room_key 找到房间，再用 room_id 拉取消息
            current_room = next((r for r in self._rooms if r.room_key == room_key), None)
            if not current_room:
                raise ValueError(f"房间不存在: {room_key}")

            messages = await self._api.get_room_messages(current_room.room_id)
            await message_view.load_messages(messages, self._agent_order)
            room_panel.mark_selected(room_key)
            room_panel.update_unread_count(room_key, 0)
            self._unread[room_key] = 0
            self._current_room_key = room_key
            self._current_msg_count = len(messages)
            status_bar.update_count(self._current_msg_count)
            team_id = self._team_ids_by_name.get(current_room.team_name)
            room_members = list(current_room.members)
            if team_id is not None:
                try:
                    room_members = await self._api.get_room_members(team_id, current_room.room_id)
                except Exception as e:
                    log.warning(
                        "获取房间成员失败，回退到 rooms/list 的 members: room_key=%s error=%s",
                        room_key,
                        e,
                    )
            self._room_members_by_key[room_key] = list(room_members)
            await room_panel.update_team_members(room_key, self._agents, room_members)

            # 查找房间信息以确定类型
            if (current_room.room_type or "").lower() == "private":
                input_container.add_class("active")
                hint_label.remove_class("active")
            else:
                input_container.remove_class("active")
                hint_label.add_class("active")
                self.query_one("#chat-input", Input).value = ""

            for i, r in enumerate(self._rooms):
                if r.room_key == room_key:
                    self._room_cursor = i
                    break
        except ValueError:
            await message_view.append_message("system", f"房间不存在: {room_key}", [])
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
                    log.debug("ws: 收到事件 room=%s sender=%s", event.room_key, event.sender)
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

        if event.event == "member_status":
            log.debug("ws: 收到成员状态变更 member=%s status=%s", event.member_name, event.status)
            # 更新本地缓存中的 Agent 状态（匹配 name + team）
            for agent in self._agents:
                if agent.name == event.member_name and agent.team_name == event.team_name:
                    agent.status = event.status or "idle"
                    break
            # 直接使用缓存刷新当前团队成员状态，无需发起 HTTP 请求
            current_members = self._room_members_by_key.get(self._current_room_key or "", [])
            self.run_worker(
                room_panel.update_team_members(self._current_room_key, list(self._agents), current_members),
                exclusive=True,
                group="member-panel",
            )
            return

        preview = _make_preview(event.sender, event.content)
        assert event.room_key is not None
        self.call_later(room_panel.update_preview, event.room_key, preview)

        if event.room_key == self._current_room_key:
            self._current_msg_count += 1
            time_str = event.time.strftime("%H:%M:%S") if event.time else ""
            self.call_later(
                message_view.append_message, event.sender, event.content, self._agent_order, time_str
            )
            self.call_later(status_bar.update_count, self._current_msg_count)
        else:
            self._unread[event.room_key] = self._unread.get(event.room_key, 0) + 1
            self.call_later(room_panel.update_unread_count, event.room_key, self._unread[event.room_key])

    @on(ListView.Selected, "#room-list")
    async def on_room_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if item.id and item.id.startswith("room-"):
            safe_id = item.id[len("room-"):]
            room_panel = self.query_one(RoomPanel)
            room_key = room_panel.room_key_from_safe(safe_id)
            if room_key:
                await self._select_room(room_key)

    @on(Input.Submitted, "#chat-input")
    async def on_input_submitted(self, event: Input.Submitted) -> None:
        content = event.value.strip()
        if not content or not self._current_room_key:
            return

        current_room = next((r for r in self._rooms if r.room_key == self._current_room_key), None)
        if not current_room:
            return

        success = await self._api.post_room_message(current_room.room_id, content)
        if success:
            self.query_one("#chat-input", Input).value = ""
        else:
            self.notify("消息发送失败", severity="error")

    def action_focus_input(self) -> None:
        current_room = next((r for r in self._rooms if r.room_key == self._current_room_key), None)
        if current_room and (current_room.room_type or "").lower() == "private":
            self.query_one("#chat-input").focus()

    async def action_prev_room(self) -> None:
        if not self._rooms:
            return
        self._room_cursor = (self._room_cursor - 1) % len(self._rooms)
        await self._select_room(self._rooms[self._room_cursor].room_key)

    async def action_next_room(self) -> None:
        if not self._rooms:
            return
        self._room_cursor = (self._room_cursor + 1) % len(self._rooms)
        await self._select_room(self._rooms[self._room_cursor].room_key)

    async def action_select_room(self) -> None:
        if not self._rooms:
            return
        await self._select_room(self._rooms[self._room_cursor].room_key)

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
        # 恢复光标为方块 (0: 恢复默认)
        sys.stdout.write("\x1b[0 q")
        sys.stdout.flush()
        await self._api.close()
