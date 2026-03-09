from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.widget import Widget
from textual.widgets import Label, ListItem, ListView, Static

from api_client import MessageInfo, AgentInfo, RoomInfo


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

    # room_id → RoomInfo，用于 set_unread 时读取房间名和人数
    _room_map: dict[str, RoomInfo]

    def load(self, rooms: list[RoomInfo], agents: list[AgentInfo]) -> None:
        self._room_map = {r.room_id: r for r in rooms}

        room_list = self.query_one("#room-list", ListView)
        agent_list = self.query_one("#agent-list", ListView)

        room_list.clear()
        agent_list.clear()

        for room in rooms:
            card = Vertical(
                Label(room.room_name, classes="room-card-name"),
                Label(f"{len(room.members)} 人", classes="room-card-members"),
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
