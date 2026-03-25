import logging
import unicodedata
from collections import defaultdict

from rich.text import Text as RichText

log = logging.getLogger("tui.widgets")
from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.widget import Widget
from textual.widgets import Label, ListItem, ListView, Static

from api_client import MessageInfo, AgentInfo, RoomInfo


def _char_width(ch: str) -> int:
    # W=宽, F=全宽 固定占2列; A=歧义宽（中文环境下终端通常渲染为2列）
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F", "A") else 1


def _truncate_to_cols(text: str, max_cols: int) -> str:
    """按显示列宽截断文字，超出时加 …。"""
    result, used = "", 0
    for ch in text:
        w = _char_width(ch)
        if used + w > max_cols - 1:
            return result + "…"
        result += ch
        used += w
    return result


class PreviewLabel(Static):
    """动态按自身宽度截断预览文字的单行 Label。"""

    def __init__(self, text: str = "", **kwargs) -> None:
        super().__init__(**kwargs)
        self._full_text = text

    def set_preview(self, text: str) -> None:
        self._full_text = text
        self.refresh()

    def render(self) -> str:
        width = self.size.width
        if width <= 0:
            return self._full_text
        return _truncate_to_cols(self._full_text, width)


def _get_side(sender: str, agent_order: list[str]) -> str:
    if sender == "system":
        return "center"
    if sender.upper() == "OPERATOR":
        return "right"
    return "left"


class MessageBubble(Vertical):
    MAX_RATIO = 0.8  # 气泡最大占消息区宽度的比例
    # A palette of colors that look good on dark backgrounds
    NAME_COLORS = [
        "#56d4b0", "#7eb8d4", "#c4a55a", "#d4847e", "#b392d4",
        "#8cc152", "#4fc1e9", "#ffce54", "#fc6e51", "#ac92ec",
        "#e8a838", "#a0d468", "#5d9cec", "#ed5565", "#fb6e52"
    ]

    def _get_name_color(self, name: str) -> str:
        """Get a deterministic color for a name."""
        if name.upper() == "OPERATOR":
            return "#7f91a4"  # Keep Operator color consistent
        # Simple hash to pick a color
        idx = sum(ord(c) for c in name) % len(self.NAME_COLORS)
        return self.NAME_COLORS[idx]

    def __init__(self, sender: str, content: str, side: str, time: str = "") -> None:
        super().__init__()
        self._sender = sender
        self._content = content
        self._side = side
        self._time = time
        self._name_color = self._get_name_color(sender)
        self._bubble_label: Label | None = None

    @property
    def _display_content(self) -> str:
        """Replace regular spaces with Non-Breaking Spaces to prevent premature wrapping."""
        return self._content.replace(" ", "\u00A0")

    def compose(self) -> ComposeResult:
        if self._side == "center":
            yield Label(f"[dim italic]{self._content}[/]", classes="bubble-system")
            return

        side_class = f"msg-{self._side}"
        
        # Row 1: Sender name and time
        with Horizontal(classes=f"msg-line {side_class}"):
            if self._side == "right":
                yield Label(f"[dim]{self._time}[/] " if self._time else "", classes="time")
                yield Label(f"[bold {self._name_color}]{self._sender}[/bold {self._name_color}]", classes="sender")
            else:
                yield Label(f"[bold {self._name_color}]{self._sender}[/bold {self._name_color}]", classes="sender")
                yield Label(f" [dim]{self._time}[/]" if self._time else "", classes="time")
        
        # Row 2: Message bubble
        with Horizontal(classes=f"msg-line {side_class}"):
            yield Label(self._display_content, classes="bubble", markup=False)

class MessageView(ScrollableContainer):
    async def load_messages(self, messages: list[MessageInfo], agent_order: list[str]) -> None:
        await self.remove_children()
        bubbles = []
        for m in messages:
            time_str = m.time.strftime("%H:%M:%S") if m.time else ""
            bubbles.append(MessageBubble(m.sender, m.content, _get_side(m.sender, agent_order), time_str))
        
        if bubbles:
            await self.mount(*bubbles)
        self.scroll_end(animate=False)

    async def append_message(self, sender: str, content: str, agent_order: list[str], time: str = "") -> None:
        bubble = MessageBubble(sender, content, _get_side(sender, agent_order), time)
        # Check if we are currently at the bottom before adding the new message
        is_at_bottom = self.scroll_y >= self.max_scroll_y
        
        await self.mount(bubble)
        
        # Only scroll to the new end if we were already at the bottom
        if is_at_bottom:
            self.scroll_end(animate=False)


class RoomPanel(Vertical):
    def compose(self) -> ComposeResult:
        yield Label("聊天室", classes="panel-title")
        yield ListView(id="room-list")
        yield Label("Agent", classes="panel-title")
        yield ListView(id="agent-list")

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._room_map: dict[str, RoomInfo] = {}
        self._safe_to_room_key: dict[str, str] = {}

    def _get_agent_status_markup(self, status: str) -> str:
        if status.lower() == "active":
            return "[bold #56d4b0]● 忙碌[/]"
        return "[#7f91a4]○ 空闲[/]"

    async def load(
        self,
        rooms: list[RoomInfo],
        agents: list[AgentInfo],
        last_previews: dict[str, str] | None = None,
    ) -> None:
        self._room_map = {r.room_key: r for r in rooms}
        if last_previews is None:
            last_previews = {}

        room_list = self.query_one("#room-list", ListView)
        agent_list = self.query_one("#agent-list", ListView)

        await room_list.clear()
        await agent_list.clear()

        # 按 team 分组展示房间
        teams: dict[str, list[RoomInfo]] = defaultdict(list)
        for room in rooms:
            teams[room.team_name].append(room)

        for team_name, team_rooms in teams.items():
            for room in team_rooms:
                icon = "👤" if room.room_type == "private" else "👥"
                preview = last_previews.get(room.room_key, "暂无消息")
                card = Vertical(
                    Horizontal(
                        Label(f"{icon} ", classes="room-card-icon"),
                        Label("", classes="room-card-name"),
                        Label(f"[dim]{len(room.members)}人[/dim]", classes="room-card-members"),
                        classes="room-card-header",
                    ),
                    PreviewLabel(preview, classes="room-card-preview"),
                    classes="room-card",
                )
                safe_id = self._safe_id(room.room_key)
                item = ListItem(card, id=f"room-{safe_id}")
                await room_list.append(item)
                self.update_unread_count(room.room_key, 0)

        # Agent 去重展示（同名 agent 跨 team 只显示一次）
        seen_agents: set[str] = set()
        for agent in agents:
            if agent.name in seen_agents:
                continue
            seen_agents.add(agent.name)
            item = ListItem(
                Horizontal(
                    Label(f"{agent.name}  [dim]{agent.model}[/dim]", classes="agent-name"),
                    Label(self._get_agent_status_markup(agent.status), classes="agent-status"),
                    classes="agent-card",
                ),
                id=f"agent-{agent.name}",
            )
            await agent_list.append(item)

    def _safe_id(self, room_key: str) -> str:
        import hashlib
        h = hashlib.md5(room_key.encode()).hexdigest()[:12]
        self._safe_to_room_key[h] = room_key
        return h

    def room_key_from_safe(self, safe_id: str) -> str | None:
        return self._safe_to_room_key.get(safe_id)

    def update_unread_count(self, room_key: str, count: int) -> None:
        try:
            safe_id = self._safe_id(room_key)
            item = self.query_one(f"#room-{safe_id}", ListItem)
            room = self._room_map.get(room_key)
            name = room.room_name if room else room_key
            icon = "👤" if room and room.room_type == "private" else "👥"
            if count > 0:
                markup = f"{name} [bold #f85149][{count}][/bold #f85149][#7f91a4] 未读[/]"
            else:
                markup = f"{name} [#7f91a4][0][/]"
            item.query_one(".room-card-icon", Label).update(f"{icon} ")
            item.query_one(".room-card-name", Label).update(markup)
        except Exception:
            log.exception("update_unread_count 失败: room_key=%s count=%s", room_key, count)

    def update_preview(self, room_key: str, preview: str) -> None:
        try:
            safe_id = self._safe_id(room_key)
            item = self.query_one(f"#room-{safe_id}", ListItem)
            item.query_one(".room-card-preview", PreviewLabel).set_preview(preview)
        except Exception:
            pass

    def update_agent_status(self, agents: list[AgentInfo]) -> None:
        for agent in agents:
            try:
                item = self.query_one(f"#agent-{agent.name}", ListItem)
                item.query_one(".agent-status", Label).update(
                    self._get_agent_status_markup(agent.status)
                )
            except Exception:
                pass

    def mark_selected(self, room_key: str) -> None:
        for item in self.query("#room-list ListItem"):
            item.remove_class("selected-room")
        try:
            safe_id = self._safe_id(room_key)
            self.query_one(f"#room-{safe_id}", ListItem).add_class("selected-room")
        except Exception:
            pass


from textual.reactive import reactive

class StatusBar(Static):
    status_markup = reactive("[bold #f85149]○ 已断开[/]")
    count = reactive(0)

    def render(self) -> str:
        return f"{self.status_markup}  |  消息数: {self.count}"

    def set_connected(self) -> None:
        self.status_markup = "[bold #56d4b0]● 已连接[/]"

    def set_reconnecting(self) -> None:
        self.status_markup = "[bold #e3b341]◌ 重连中…[/]"

    def set_disconnected(self, countdown: int | None = None) -> None:
        if countdown:
            self.status_markup = f"[bold #f85149]○ 已断开，{countdown}s 后重连[/]"
        else:
            self.status_markup = "[bold #f85149]○ 已断开[/]"

    def update_count(self, n: int) -> None:
        self.count = n
