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


def _char_wrap(text: str, width: int) -> str:
    """按字符边界换行，正确处理 CJK 双宽字符，忽略词边界。"""
    lines = []
    for paragraph in text.split("\n"):
        line, used = "", 0
        for ch in paragraph:
            w = _char_width(ch)
            if used + w > width:
                lines.append(line)
                line, used = ch, w
            else:
                line += ch
                used += w
        lines.append(line)
    return "\n".join(lines)


class BubbleText(Static):
    """气泡内容：按字符边界换行，避免 ASCII+CJK 混排时的词边界断行问题。"""

    def __init__(self, text: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._text = text

    def render(self) -> str:
        width = self.size.width
        if width <= 0:
            return self._text
        return _char_wrap(self._text, width)


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
    if sender == "Operator":
        return "right"
    try:
        idx = agent_order.index(sender)
    except ValueError:
        return "left"
    return "left" if idx % 2 == 0 else "right"


class MessageBubble(Vertical):
    MAX_RATIO = 0.6  # 气泡最大占消息区宽度的比例

    def __init__(self, sender: str, content: str, side: str) -> None:
        super().__init__()
        self._sender = sender
        self._content = content
        self._side = side
        self._last_inner_w: int = 0

    def on_resize(self, event) -> None:
        if self._side == "center":
            return
        new_w = max(10, int(event.size.width * self.MAX_RATIO))
        if new_w == self._last_inner_w:
            return
        self._last_inner_w = new_w
        for inner in self.query(".bubble-inner"):
            inner.styles.width = new_w

    def compose(self) -> ComposeResult:
        if self._side == "center":
            yield Static(f"[dim italic]{self._content}[/]", classes="bubble-system")
        elif self._side == "right":
            with Horizontal(classes="bubble-row"):
                yield Static("", classes="bubble-spacer")
                with Vertical(classes="bubble-inner"):
                    yield Static(f"[bold #c4a55a]{self._sender}[/bold #c4a55a]", classes="sender sender-right")
                    yield BubbleText(self._content, classes="bubble bubble-right")
        else:
            with Horizontal(classes="bubble-row"):
                with Vertical(classes="bubble-inner"):
                    yield Static(f"[bold #7eb8d4]{self._sender}[/bold #7eb8d4]", classes="sender sender-left")
                    yield BubbleText(self._content, classes="bubble bubble-left")
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

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._room_map: dict[str, RoomInfo] = {}

    def _get_agent_status_markup(self, status: str) -> str:
        if status == "active":
            return "[bold #56d4b0]● 活跃[/]"
        return "[#484f58]○ 空闲[/]"

    async def load(
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

        await room_list.clear()
        await agent_list.clear()

        for room in rooms:
            preview = last_previews.get(room.room_id, "暂无消息")
            card = Vertical(
                Horizontal(
                    Label("", classes="room-card-name"),
                    Label(f"[dim]{len(room.members)}人[/dim]", classes="room-card-members"),
                    classes="room-card-header",
                ),
                PreviewLabel(preview, classes="room-card-preview"),
                classes="room-card",
            )
            item = ListItem(card, id=f"room-{room.room_id}")
            await room_list.append(item)
            self.update_unread_count(room.room_id, 0)

        for agent in agents:
            item = ListItem(
                Horizontal(
                    Label(f"{agent.name}  [dim]{agent.model}[/dim]", classes="agent-name"),
                    Label(self._get_agent_status_markup(agent.status), classes="agent-status"),
                    classes="agent-card",
                ),
                id=f"agent-{agent.name}",
            )
            await agent_list.append(item)

    def update_unread_count(self, room_id: str, count: int) -> None:
        try:
            item = self.query_one(f"#room-{room_id}", ListItem)
            room = self._room_map.get(room_id)
            name = room.room_name if room else room_id
            if count > 0:
                markup = f"{name} [bold red][未读:{count}][/bold red]"
            else:
                markup = f"{name} [#6e7681][未读:0][/]"
            item.query_one(".room-card-name", Label).update(markup)
        except Exception:
            pass

    def update_preview(self, room_id: str, preview: str) -> None:
        try:
            item = self.query_one(f"#room-{room_id}", ListItem)
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

    def mark_selected(self, room_id: str) -> None:
        for item in self.query("#room-list ListItem"):
            item.remove_class("selected-room")
        try:
            self.query_one(f"#room-{room_id}", ListItem).add_class("selected-room")
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
