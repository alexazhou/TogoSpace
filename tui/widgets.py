from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.widget import Widget
from textual.widgets import Label, ListItem, ListView, Static

from api_client import MessageInfo, AgentInfo, RoomInfo


def _char_width(ch: str) -> int:
    cp = ord(ch)
    return 2 if (
        0x1100 <= cp <= 0x115F or 0x2E80 <= cp <= 0x303E
        or 0x3040 <= cp <= 0xA4CF or 0xA960 <= cp <= 0xA97F
        or 0xAC00 <= cp <= 0xD7FF or 0xF900 <= cp <= 0xFAFF
        or 0xFE10 <= cp <= 0xFE1F or 0xFE30 <= cp <= 0xFE6F
        or 0xFF01 <= cp <= 0xFF60 or 0xFFE0 <= cp <= 0xFFE6
    ) else 1


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
    try:
        idx = agent_order.index(sender)
    except ValueError:
        return "left"
    return "left" if idx % 2 == 0 else "right"


class MessageBubble(Vertical):
    def __init__(self, sender: str, content: str, side: str) -> None:
        super().__init__()
        self._sender = sender
        self._content = content
        self._side = side

    def compose(self) -> ComposeResult:
        if self._side == "center":
            yield Static(f"[dim italic]{self._content}[/]", classes="bubble-system")
        elif self._side == "right":
            with Horizontal(classes="bubble-row"):
                yield Static("", classes="bubble-spacer")
                with Vertical(classes="bubble-inner"):
                    yield Static(f"[bold cyan]{self._sender}[/bold cyan]", classes="sender sender-right")
                    yield Static(self._content, classes="bubble bubble-right")
        else:
            with Horizontal(classes="bubble-row"):
                with Vertical(classes="bubble-inner"):
                    yield Static(f"[bold green]{self._sender}[/bold green]", classes="sender sender-left")
                    yield Static(self._content, classes="bubble bubble-left")
                yield Static("", classes="bubble-spacer")


class MessageView(ScrollableContainer):
    async def load_messages(self, messages: list[MessageInfo], agent_order: list[str]) -> None:
        await self.remove_children()
        bubbles = [
            MessageBubble(m.sender, m.content, _get_side(m.sender, agent_order))
            for m in messages
        ]
        if bubbles:
            await self.mount(*bubbles)
        self.scroll_end(animate=False)

    async def append_message(self, sender: str, content: str, agent_order: list[str]) -> None:
        bubble = MessageBubble(sender, content, _get_side(sender, agent_order))
        await self.mount(bubble)
        self.scroll_end(animate=True)


class RoomPanel(Vertical):
    def compose(self) -> ComposeResult:
        yield Label("聊天室", classes="panel-title")
        yield ListView(id="room-list")
        yield Label("Agent", classes="panel-title")
        yield ListView(id="agent-list")

    # room_id → RoomInfo，用于 set_unread 时读取房间名
    _room_map: dict[str, RoomInfo]

    def load(
        self,
        rooms: list[RoomInfo],
        agents: list[AgentInfo],
        last_previews: dict[str, str] | None = None,
    ) -> None:
        self._room_map = {r.room_id: r for r in rooms}
        if last_previews is None:
            last_previews = {}

        room_list = self.query_one("#room-list", ListView)
        agent_list = self.query_one("#agent-list", ListView)

        room_list.clear()
        agent_list.clear()

        for room in rooms:
            preview = last_previews.get(room.room_id, "暂无消息")
            card = Vertical(
                Label(room.room_name, classes="room-card-name"),
                PreviewLabel(preview, classes="room-card-preview"),
                classes="room-card",
            )
            item = ListItem(card, id=f"room-{room.room_id}")
            room_list.append(item)

        for agent in agents:
            item = ListItem(Label(f"{agent.name}  [{agent.model}]"))
            agent_list.append(item)

    def set_unread(self, room_id: str, n: int) -> None:
        try:
            item = self.query_one(f"#room-{room_id}", ListItem)
            name_label = item.query_one(".room-card-name", Label)
            room_name = getattr(self, "_room_map", {}).get(room_id)
            base = room_name.room_name if room_name else room_id
            name_label.update(f"{base} [{n}]")
        except Exception:
            pass

    def clear_unread(self, room_id: str) -> None:
        try:
            item = self.query_one(f"#room-{room_id}", ListItem)
            name_label = item.query_one(".room-card-name", Label)
            room_name = getattr(self, "_room_map", {}).get(room_id)
            base = room_name.room_name if room_name else room_id
            name_label.update(base)
        except Exception:
            pass

    def update_preview(self, room_id: str, preview: str) -> None:
        try:
            item = self.query_one(f"#room-{room_id}", ListItem)
            item.query_one(".room-card-preview", PreviewLabel).set_preview(preview)
        except Exception:
            pass

    def mark_selected(self, room_id: str) -> None:
        for item in self.query("#room-list ListItem"):
            item.remove_class("selected-room")
        try:
            self.query_one(f"#room-{room_id}", ListItem).add_class("selected-room")
        except Exception:
            pass


class StatusBar(Static):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._status = "○ 已断开"
        self._count: int | None = None

    def _build_text(self) -> str:
        if self._count is None:
            return self._status
        return f"{self._status}  |  消息数: {self._count}"

    def set_connected(self) -> None:
        self._status = "● 已连接"
        self.update(self._build_text())

    def set_reconnecting(self) -> None:
        self._status = "◌ 重连中…"
        self.update(self._build_text())

    def set_disconnected(self) -> None:
        self._status = "○ 已断开"
        self.update(self._build_text())

    def update_count(self, n: int) -> None:
        self._count = n
        self.update(self._build_text())
