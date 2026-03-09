# V5 终端可视化前端 — 技术文档

---

## 架构设计

### 整体架构

V5 是一个**独立的终端客户端进程**，通过 V4 暴露的 HTTP 和 WebSocket 接口与后端通信，不修改任何后端代码。

```
┌─────────────────────────────────────────────────────┐
│               V5 客户端进程（tui/main.py）              │
│                                                     │
│  ┌────────────────────────────────────────────────┐ │
│  │             WatcherApp（Textual App）            │ │
│  │                                                │ │
│  │  ┌──────────────┐  ┌───────────────────────┐  │ │
│  │  │  RoomPanel   │  │    MessageView         │  │ │
│  │  │  (左侧面板)   │  │    (右侧消息区)         │  │ │
│  │  │  · 房间列表   │  │    · MessageBubble × N │  │ │
│  │  │  · 成员列表   │  └───────────────────────┘  │ │
│  │  │  · Agent列表  │  ┌───────────────────────┐  │ │
│  │  └──────────────┘  │    StatusBar           │  │ │
│  │                    │    (底部状态栏)          │  │ │
│  │                    └───────────────────────┘  │ │
│  └─────────────────────────────────────┬──────────┘ │
│                                        │            │
│  ┌─────────────────────────────────────▼──────────┐ │
│  │               ApiClient                        │ │
│  │   get_agents() · get_rooms()                   │ │
│  │   get_room_messages() · connect_ws()           │ │
│  └────────────────────────────────────────────────┘ │
└──────────────────────────┬──────────────────────────┘
                           │  HTTP / WebSocket
              ┌────────────▼────────────┐
              │  V4 后端（main.py）      │
              │  GET /agents            │
              │  GET /rooms             │
              │  GET /rooms/{id}/msgs   │
              │  WS  /ws/events         │
              └─────────────────────────┘
```

### 异步集成

Textual 内部使用 asyncio 事件循环驱动 UI 渲染与事件处理。aiohttp 的 HTTP 和 WebSocket 客户端同样基于 asyncio，可以直接在 Textual 的 `on_mount`、`@work(thread=False)` 等协程钩子中调用，无需另起线程。

WebSocket 接收循环通过 `self.app.call_from_thread` 或直接在协程 worker 中调用 `self.post_message` / `self.app.query_one(...).method()` 将新消息投递到 UI 线程安全的消息队列，Textual 保证 widget 更新在事件循环主线程中执行。

---

## 目录结构

```
agent_team/
└── tui/
    ├── main.py          # 入口：解析 --base-url 参数，启动 WatcherApp
    ├── app.py           # WatcherApp 定义（App 子类，CSS、compose、事件处理）
    ├── widgets.py       # RoomPanel、MessageView、MessageBubble、StatusBar
    └── api_client.py    # ApiClient（aiohttp HTTP + WebSocket 封装）
```

---

## 模块接口

### api_client.py

```python
import aiohttp
from dataclasses import dataclass
from datetime import datetime
from typing import AsyncIterator


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
        """
        base_url: 后端地址，如 "http://127.0.0.1:8080"，末尾不含斜杠。
        """

    async def get_agents(self) -> list[AgentInfo]:
        """GET /agents → 返回 Agent 列表。连接失败抛出 aiohttp.ClientError。"""

    async def get_rooms(self) -> list[RoomInfo]:
        """GET /rooms → 返回房间列表。"""

    async def get_room_messages(self, room_id: str) -> list[MessageInfo]:
        """GET /rooms/{room_id}/messages → 返回消息历史。房间不存在抛出 ValueError。"""

    async def ws_events(self) -> AsyncIterator[WsEvent]:
        """
        连接 WS /ws/events，异步迭代推送事件。
        连接断开时迭代结束（不抛出异常），调用方负责重连逻辑。
        """

    async def close(self) -> None:
        """关闭内部 aiohttp.ClientSession。"""
```

### widgets.py

```python
from textual.widgets import Static, ListView, ListItem, Label
from textual.containers import ScrollableContainer, Vertical


class MessageBubble(Static):
    """单条消息气泡。

    参数:
        sender: 发送者名称
        content: 消息正文
        side: "left" | "right" | "center"
            left  — 奇数序号 Agent，绿色用户名，左对齐
            right — 偶数序号 Agent，青色用户名，右对齐
            center — system 消息，灰色斜体，居中
    """
    def __init__(self, sender: str, content: str, side: str) -> None: ...
    def compose(self) -> ComposeResult: ...


class MessageView(ScrollableContainer):
    """可滚动消息区域。提供辅助方法追加气泡并自动滚动到底部。"""

    async def load_messages(self, messages: list[MessageInfo], agent_order: list[str]) -> None:
        """清空现有内容，批量挂载气泡，滚动到底部。"""

    async def append_message(self, sender: str, content: str, agent_order: list[str]) -> None:
        """追加单条气泡，滚动到底部。"""


class RoomPanel(Vertical):
    """左侧面板：房间列表（含未读角标）+ Agent 列表。

    公开方法:
        load(rooms, agents)        — 初始化填充列表
        set_unread(room_id, n)     — 更新未读计数
        clear_unread(room_id)      — 清除未读角标
        mark_selected(room_id)     — 更新选中高亮
    """


class StatusBar(Static):
    """底部一行状态栏。

    公开方法:
        set_connected()   — 显示"已连接"
        set_reconnecting() — 显示"重连中…"
        set_disconnected() — 显示"已断开"
        update_count(room_id, n) — 更新当前房间消息数
    """
```

### app.py

```python
from textual.app import App, ComposeResult


class WatcherApp(App):
    """终端聊天室观察台主 App。

    启动时序：
        on_mount()
            → api_client.get_agents()       → 填充 RoomPanel 底部
            → api_client.get_rooms()        → 填充 RoomPanel 房间列表
            → _select_room(first_room_id)   → 加载第一个房间消息
            → _start_ws_loop()              → 启动 WebSocket 接收 worker
    """

    def __init__(self, base_url: str) -> None: ...
    def compose(self) -> ComposeResult: ...
    async def on_mount(self) -> None: ...
```

### main.py

启动时从 `config.json` 的 `server` 字段读取后端地址，`--base-url` 命令行参数可覆盖配置文件：

```python
import argparse
import json
import os
from app import WatcherApp

_DEFAULT_CONFIG = os.path.join(os.path.dirname(__file__), "../config.json")


def _load_base_url(config_path: str) -> str:
    try:
        with open(config_path, encoding="utf-8") as f:
            cfg = json.load(f)
        srv = cfg.get("server", {})
        host = srv.get("host", "127.0.0.1")
        port = srv.get("port", 8080)
        return f"http://{host}:{port}"
    except (FileNotFoundError, KeyError, ValueError):
        return "http://127.0.0.1:8080"


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent 聊天室终端观察台")
    parser.add_argument("--base-url", default=None, dest="base_url",
                        help="后端地址，默认从 config.json 读取")
    parser.add_argument("--config", default=_DEFAULT_CONFIG,
                        help="config.json 路径")
    args = parser.parse_args()

    base_url = args.base_url or _load_base_url(args.config)
    app = WatcherApp(base_url=base_url)
    app.run()


if __name__ == "__main__":
    main()
```

优先级：`--base-url` 命令行参数 > `config.json` 中 `server` 字段 > 内置默认值（`127.0.0.1:8080`）。

---

## 组件详细设计

### WatcherApp

**响应式状态**

| 属性 | 类型 | 说明 |
|------|------|------|
| `current_room_id` | `reactive[str \| None]` | 当前选中的房间 ID |
| `_agent_order` | `list[str]` | Agent 列表（按 GET /agents 返回顺序），用于计算气泡左右侧 |
| `_unread` | `dict[str, int]` | 各房间未读消息计数 |

**启动时序**

```
on_mount()
    │
    ├─ try: get_agents()  → _agent_order = [a.name for a in agents]
    │                      → room_panel.load(rooms, agents)
    ├─ try: get_rooms()   → 填充房间列表
    │
    ├─ if rooms 非空:
    │     _select_room(rooms[0].room_id)
    │
    ├─ _start_ws_loop()   → @work 协程，持续接收 WsEvent
    │
    └─ except ClientError: status_bar.set_disconnected()，显示错误提示
```

**房间切换 `_select_room(room_id)`**

```
1. api_client.get_room_messages(room_id)
2. message_view.load_messages(messages, _agent_order)
3. room_panel.mark_selected(room_id)
4. room_panel.clear_unread(room_id)
5. _unread[room_id] = 0
6. status_bar.update_count(room_id, len(messages))
7. current_room_id = room_id
```

**WebSocket 接收 worker `_start_ws_loop()`**

```python
@work(exclusive=True)
async def _start_ws_loop(self) -> None:
    while True:
        try:
            status_bar.set_connected()
            async for event in api_client.ws_events():
                self._on_ws_event(event)
        except Exception:
            status_bar.set_disconnected()
            await asyncio.sleep(3)        # 3s 后重连
            status_bar.set_reconnecting()
```

**WsEvent 处理 `_on_ws_event(event)`**

```
if event.room_id == current_room_id:
    message_view.append_message(event.sender, event.content, _agent_order)
    status_bar.update_count(current_room_id, ...)
else:
    _unread[event.room_id] += 1
    room_panel.set_unread(event.room_id, _unread[event.room_id])
```

**键盘事件**

```python
BINDINGS = [
    ("q",       "quit",        "退出"),
    ("up",      "prev_room",   "上一个房间"),
    ("down",    "next_room",   "下一个房间"),
    ("enter",   "select_room", "切换到当前房间"),
    ("ctrl+c",  "quit",        "退出"),
]
```

PageUp / PageDown 由 Textual 的 `ScrollableContainer` 内建支持，无需额外绑定。

---

### MessageBubble

气泡的左右侧由调用方（`MessageView`）根据 `sender` 在 `agent_order` 中的索引决定：

```python
def _get_side(sender: str, agent_order: list[str]) -> str:
    if sender == "system":
        return "center"
    try:
        idx = agent_order.index(sender)   # 0-based
    except ValueError:
        return "left"
    return "left" if idx % 2 == 0 else "right"
```

> 产品文档"奇数序号靠左、偶数序号靠右"以 **1-based 序号**描述，等价于 0-based 索引：`idx % 2 == 0` → 左，`idx % 2 == 1` → 右。

气泡 `compose()`：

```python
def compose(self) -> ComposeResult:
    if self._side == "center":
        yield Static(f"[dim italic]{self._content}[/]", classes="bubble-system")
    elif self._side == "right":
        yield Static(f"[bold cyan]{self._sender}[/bold cyan]", classes="sender sender-right")
        yield Static(self._content, classes="bubble bubble-right")
    else:  # left
        yield Static(f"[bold green]{self._sender}[/bold green]", classes="sender sender-left")
        yield Static(self._content, classes="bubble bubble-left")
```

---

### RoomPanel

左侧面板分为两个区域，以 `Vertical` 嵌套：

```
RoomPanel (Vertical)
├── Label "聊天室"           # 标题
├── ListView #room-list      # 房间列表，可选中
│   ├── ListItem #room-{id}  # 每个房间条目（含未读角标）
│   └── ...
├── Label "Agent"            # 分隔标题
└── ListView #agent-list     # Agent 列表（仅展示）
    ├── ListItem             # {name}  [{model}]
    └── ...
```

未读角标以文本形式嵌在 `ListItem` 的 `Label` 内：`general [3]`。更新时直接 `label.update(new_text)`。

当前选中房间通过 `ListItem` 的 CSS class `selected-room` 控制样式（高亮背景），切换时移除旧项 class、添加新项 class：

```python
def mark_selected(self, room_id: str) -> None:
    for item in self.query("#room-list ListItem"):
        item.remove_class("selected-room")
    self.query_one(f"#room-{room_id}", ListItem).add_class("selected-room")
```

---

## CSS 布局

```css
Screen {
    background: $surface;
}

/* ── 整体水平分栏 ── */
#main-horizontal {
    height: 1fr;
}

/* ── 左侧面板 ── */
RoomPanel {
    width: 22;
    min-width: 18;
    border-right: solid $panel;
}

RoomPanel .panel-title {
    width: 100%;
    text-align: center;
    background: $panel;
    color: $text;
    padding: 0 1;
    text-style: bold;
}

RoomPanel ListView {
    background: $surface;
    border: none;
}

#room-list {
    height: 1fr;
}

#agent-list {
    height: auto;
    max-height: 10;
    border-top: solid $panel;
}

RoomPanel ListItem {
    padding: 0 1;
}

.selected-room {
    background: $accent-darken-3;
}

/* ── 右侧区域 ── */
#right-panel {
    width: 1fr;
}

/* ── 消息区 ── */
MessageView {
    height: 1fr;
    padding: 1 2;
    background: $surface-darken-1;
}

MessageBubble {
    margin-bottom: 1;
    width: 100%;
}

.sender {
    text-style: bold;
}

.sender-left {
    text-align: left;
    width: 100%;
}

.sender-right {
    text-align: right;
    width: 100%;
}

.bubble {
    padding: 0 1;
}

.bubble-left {
    width: 60%;
    background: $panel;
    text-align: left;
}

.bubble-right {
    width: 60%;
    background: $accent-darken-3;
    text-align: right;
    offset-x: 40%;
}

.bubble-system {
    width: 100%;
    text-align: center;
    color: $text-muted;
}

/* ── 状态栏 ── */
StatusBar {
    height: 1;
    background: $panel;
    padding: 0 2;
    color: $text-muted;
}
```

---

## 数据流

### 启动阶段

```
WatcherApp.on_mount()
    │
    ├─ GET /agents  ──────► _agent_order = ["alice", "bob", "charlie", ...]
    │                        RoomPanel.load(rooms=[], agents=agents)
    │
    ├─ GET /rooms   ──────► rooms = [RoomInfo(room_id="general", ...), ...]
    │                        RoomPanel.load(rooms=rooms, agents=agents)
    │
    ├─ GET /rooms/general/messages
    │               ──────► MessageView.load_messages(messages, _agent_order)
    │                        StatusBar.update_count("general", len(messages))
    │
    └─ WS /ws/events ─────► _start_ws_loop() 进入接收循环
                             StatusBar.set_connected()
```

### 实时消息推送

```
后端广播 WsEvent
    │
    ▼
_on_ws_event(event)
    │
    ├─ event.room_id == current_room_id ?
    │       YES ─► MessageView.append_message(...)
    │              StatusBar.update_count(...)
    │
    └─  NO  ─► _unread[event.room_id] += 1
               RoomPanel.set_unread(event.room_id, count)
```

### 房间切换

```
用户按 ↑/↓/Enter  或  点击 ListItem
    │
    ▼
_select_room(room_id)
    │
    ├─ GET /rooms/{room_id}/messages
    ├─ MessageView.load_messages(...)        # 清空旧消息，批量挂载
    ├─ RoomPanel.mark_selected(room_id)
    ├─ RoomPanel.clear_unread(room_id)
    ├─ _unread[room_id] = 0
    └─ StatusBar.update_count(room_id, ...)
```

---

## 关键设计决策

### Textual Worker 承载 WebSocket 循环

Textual 的 `@work` 装饰器将协程作为后台 worker 运行，Textual 会在 App 退出时自动取消所有 worker，无需手动管理生命周期。使用 `exclusive=True` 确保只有一个 WS 循环运行。

Worker 内可直接操作 widget（Textual 保证所有 widget 操作均在主事件循环中执行），不需要 `call_from_thread`。

### 初始化失败降级

`on_mount` 用 `try/except aiohttp.ClientError` 包裹所有 HTTP 请求，连接失败时：
- `StatusBar.set_disconnected()` 更新状态栏
- `MessageView` 展示居中错误提示（system 消息样式）
- 程序不崩溃，WS 循环仍会尝试每 3s 重连

### 未读计数不跨会话持久化

未读计数只在内存中维护（`_unread: dict[str, int]`），重启 TUI 后清零。这符合"纯观察模式，不写入后端"的设计约束。

### 气泡宽度与右对齐实现

Textual 的 CSS 不支持 `margin-left: auto`。右对齐气泡通过 `offset-x: 40%` 将 60% 宽的气泡右移，视觉上靠右，同时保持 `text-align: right`。

### agent_order 与 side 映射

`agent_order` 来自 `GET /agents` 的返回顺序，在 App 级别持有，传入 `MessageView.load_messages` 和 `append_message`。若后端新增 Agent 但 TUI 未刷新，新 Agent 的 `sender` 不在列表中时默认靠左，不崩溃。

---

## 错误处理

| 场景 | 处理方式 |
|------|----------|
| 启动时后端不可达 | `StatusBar` 显示"已断开"，消息区显示连接失败提示，程序不崩溃 |
| 房间切换时 HTTP 失败 | `MessageView` 显示错误提示，`current_room_id` 不变 |
| WS 连接断开 | `StatusBar` 显示"已断开"，3s 后自动重连，重连期间显示"重连中…" |
| 房间不存在（404） | `MessageView` 显示"房间不存在"提示 |
| Agent 不在 agent_order 中 | 气泡默认靠左，不抛出异常 |

---

## 目录结构（完整）

```
agent_team/
├── tui/
│   ├── main.py          # 入口：argparse + WatcherApp.run()
│   ├── app.py           # WatcherApp（App 子类，CSS + 所有事件处理）
│   ├── widgets.py       # RoomPanel / MessageView / MessageBubble / StatusBar
│   └── api_client.py    # ApiClient（aiohttp HTTP + WS，纯数据层，无 UI 依赖）
└── docs/
    └── versions/
        └── v5/
            ├── v5_step1_product.md
            └── v5_step2_technical.md    # 本文档
```

---

## 修改的模块

V5 全部为**新增文件**，不修改任何现有 src/ 代码。

| 文件 | 说明 |
|------|------|
| `tui/main.py` | 新增，入口脚本 |
| `tui/app.py` | 新增，`WatcherApp` 主 App |
| `tui/widgets.py` | 新增，所有自定义 Widget |
| `tui/api_client.py` | 新增，后端 HTTP/WS 客户端封装 |

### 依赖

无需新增依赖。`textual` 和 `aiohttp` 均已在 `requirements.txt` 中。

---

## 运行与验证

```bash
# 启动后端（V4）
cd /Volumes/PData/GitDB/agent_team/src && python main.py

# 另开终端启动 TUI（从 config.json 读取后端地址）
python tui/main.py

# 指定自定义 config.json
python tui/main.py --config /path/to/config.json

# 临时覆盖后端地址（优先级最高）
python tui/main.py --base-url http://127.0.0.1:9090
```

验收步骤对应产品文档第 9 节，核心验收场景：

1. 后端运行时启动 TUI，左侧展示房间和成员列表，右侧展示历史消息，底部显示"已连接"
2. 键盘切换房间，消息区随即更新，自动滚动到底部
3. 后端 Agent 在当前房间发言，新气泡实时追加
4. Agent 在非当前房间发言，左侧对应房间出现未读角标
5. 切换到有角标的房间，角标清除
6. 后端未启动时运行 TUI，显示连接失败提示，不崩溃
