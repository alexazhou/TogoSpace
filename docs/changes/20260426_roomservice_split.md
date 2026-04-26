# roomService 拆分方案

## 背景

`src/service/roomService.py` 目前是一个 929 行的单文件模块，包含：

1. **`ChatRoom` 类（约 550 行）**：承担了房间元数据、消息管理、轮次调度、状态机、持久化协调等五类职责，并包含多个高复杂度方法。
2. **模块级函数（约 380 行）**：房间注册表（`_rooms` / `_rooms_by_id`）、生命周期（startup/shutdown）、加载恢复、CRUD 编排。

单文件无法清晰展示职责边界，新功能叠加导致维护成本持续上升。

## 问题分析

### ChatRoom 的五类职责

| 职责类别 | 代表方法 | 行数 |
|----------|---------|------|
| 元数据只读访问 | `room_id`, `team_id`, `name`, `agents` 等 14 个属性 | ~60 行 |
| 消息管理 | `add_message`, `_append_message`, `get_unread_messages`, `mark_all_messages_read` | ~70 行 |
| 轮次调度 | `finish_turn`, `activate_scheduling`, `_resolve_next_dispatchable_agent`, `_go_next_turn`, `_should_stop_scheduling` | ~130 行 |
| 状态机 | `_update_turn_state_on_message`（跨越消息与调度） | ~30 行 |
| 持久化协调 | `inject_runtime_state`, `rebuild_state_from_history`, `export_agent_read_index`, `_persist_turn_pos` | ~60 行 |

### 核心耦合点

`_update_turn_state_on_message()` 同时操作消息侧（`_agent_read_index`）和调度侧（`_state`, `_turn_count`, `_round_skipped_set`, `_current_turn_has_content`），是两个职责之间的天然连接点。

消息 append 会内部调用调度状态更新：

```
add_message()
  → _append_message(update_turn_state=True)
    → _update_turn_state_on_message(sender_id)   ← 同时写调度状态
```

### 模块级函数的问题

模块级函数与全局变量（`_rooms`、`_rooms_by_id`）散落在文件后半段（L619-929），与 `ChatRoom` 类没有视觉分隔，难以快速定位。

## 设计目标

1. **零接口破坏**：所有外部调用（controller / service / tests）无需修改。
2. **拆分 ChatRoom 内部职责**：提取 `RoomTurnScheduler` 和 `RoomMessageStore`，`ChatRoom` 退化为协调 Facade。
3. **模块级函数独立成文件**：提升可读性，便于后续扩展。
4. **保留现有测试覆盖**：文件组织变化不影响测试断言。

## 非目标

- 不更改任何公共 API 签名（方法名、参数、返回类型）
- 不修改 DAL 层（`gtRoomManager`、`gtRoomMessageManager` 等）
- 不调整调度逻辑本身（`_resolve_next_dispatchable_agent` 的行为不变）
- 不合并 M3（N+1 查询），该问题独立评估

---

## 方案设计

### 目录结构

```text
src/service/
├── roomService/
│   ├── __init__.py          # 重导出所有公共符号，对外接口完全不变
│   ├── core.py              # 模块级函数 + 全局注册表（原 L619-929）
│   ├── chatRoom.py          # ChatRoom 类（持有 scheduler + store，负责协调）
│   ├── scheduler.py         # RoomTurnScheduler（轮次调度状态机）
│   └── messageStore.py      # RoomMessageStore（消息缓冲 + 未读索引）
└── roomService.py           # 删除（被目录替代）
```

### 各模块职责

#### `core.py` — 房间注册表与生命周期

原模块级变量和函数（L619-929）原样迁移，无逻辑改动：

- 全局变量：`_rooms: Dict[str, ChatRoom]`、`_rooms_by_id: Dict[int, ChatRoom]`
- 生命周期：`startup()`, `shutdown()`
- 加载与恢复：`_load_room()`, `load_team_rooms()`, `load_all_rooms()`, `_restore_room_runtime_state()`, `restore_team_rooms_runtime_state()`, `restore_all_rooms_runtime_state()`
- 查询：`get_room()`, `get_room_by_key()`, `get_all_rooms()`, `get_agent_names()`, `get_rooms_for_agent()`, `get_room_messages_from_db()`
- CRUD 编排：`create_team_rooms()`, `overwrite_team_rooms()`, `overwrite_dept_rooms()`, `batch_create_rooms()`, `update_room_agents()`
- 激活入口：`activate_rooms()`

另保留原模块顶部的工具函数：

- `resolve_room_max_turns()`
- `_same_speaker()`
- `_infer_room_type()`
- `ToolCallContext`（dataclass）

#### `scheduler.py` — 轮次调度状态机

提取出 `RoomTurnScheduler` 类，管理所有"谁该说话、说了多少轮"的纯内存状态：

**管辖的状态变量（从 ChatRoom 迁出）：**

| 变量 | 含义 |
|------|------|
| `_state: RoomState` | 房间调度状态（INIT / SCHEDULING / IDLE） |
| `_turn_pos: int` | 当前发言位下标 |
| `_turn_count: int` | 已完成完整轮次数 |
| `_round_skipped_set: set[int]` | 本轮被跳过的 agent_id 集合 |
| `_current_turn_has_content: bool` | 当前发言者是否已发言 |

**管辖的方法（从 ChatRoom 迁移为内部方法）：**

- `_go_next_turn()` — 推进发言位
- `_should_stop_scheduling()` — 判断停止条件（最大轮次 / 全部跳过）
- `_should_auto_skip_agent_turn()` — 判断是否自动跳过 OPERATOR
- `_resolve_next_dispatchable_agent()` — 核心调度决策
- `rebuild_turn_state(persisted_turn_pos)` — 从持久化数据恢复（原 `rebuild_state_from_history` 中的调度部分）

**暴露给 ChatRoom 的公共接口：**

```python
class RoomTurnScheduler:
    @property
    def state(self) -> RoomState: ...
    @state.setter
    def state(self, value: RoomState): ...

    @property
    def turn_pos(self) -> int: ...

    @property
    def turn_count(self) -> int: ...

    def get_current_turn_agent_id(self) -> int | None: ...
    def go_next_turn(self) -> None: ...
    def resolve_next_dispatchable_agent(self) -> int | None: ...
    def set_has_content(self, value: bool) -> None: ...  # 设置 _current_turn_has_content
    def mark_agent_skipped(self, agent_id: int) -> None: ...
    def is_idle(self) -> bool: ...
    def wake_up(self) -> None: ...               # 从 IDLE → SCHEDULING，重置计数器
    def rebuild_turn_state(self, persisted_turn_pos: int | None) -> None: ...
```

`RoomTurnScheduler.__init__` 接收 `agent_ids`、`max_turns`、`room_key`、`is_group_room_with_operator` 等只读配置参数（不持有 ChatRoom 引用），保证单向依赖。

#### `messageStore.py` — 消息缓冲与未读索引

提取出 `RoomMessageStore` 类，管理内存消息列表和每个 agent 的已读指针：

**管辖的状态变量（从 ChatRoom 迁出）：**

| 变量 | 含义 |
|------|------|
| `messages: List[GtCoreRoomMessage]` | 房间全量消息列表 |
| `_agent_read_index: Dict[int, int]` | 每个 agent 的已读位置 |

**暴露给 ChatRoom 的公共接口：**

```python
class RoomMessageStore:
    @property
    def messages(self) -> List[GtCoreRoomMessage]: ...

    def append(self, msg: GtCoreRoomMessage) -> None: ...
    def get_unread(self, agent_id: int) -> List[GtCoreRoomMessage]: ...
    def mark_all_read(self) -> None: ...
    def inject(
        self,
        messages: List[GtCoreRoomMessage] | None,
        agent_read_index: Dict[str, int] | None,
    ) -> None: ...
    def export_read_index(self, stable_name_fn: Callable[[int], str]) -> Dict[str, int]: ...
    def get_read_index(self) -> Dict[int, int]: ...   # 供 _persist_turn_pos 读取
```

#### `chatRoom.py` — 协调 Facade

`ChatRoom` 持有两个子组件，负责跨职责的协调逻辑：

```python
class ChatRoom:
    def __init__(self, gt_team, gt_room, gt_agents):
        ...
        self._scheduler = RoomTurnScheduler(
            agent_ids=self._agent_ids,
            max_turns=gt_room.max_turns,
            room_key=self.key,
            is_group_room_with_operator=...,
        )
        self._store = RoomMessageStore(agent_ids=self._agent_ids)
```

**保留在 ChatRoom 的方法（协调逻辑）：**

| 方法 | 保留原因 |
|------|---------|
| `_update_turn_state_on_message()` | 同时操作 store 和 scheduler，需跨两个子组件协调 |
| `finish_turn()` | 调 scheduler.go_next_turn + persist + publish，需协调 scheduler + DB + messageBus |
| `activate_scheduling()` | 消息追加 + 调度激活 + publish，跨 store + scheduler + messageBus |
| `inject_runtime_state()` | 委托 store.inject + scheduler.turn_pos 注入，整合入口 |
| `rebuild_state_from_history()` | 调 scheduler.rebuild_turn_state，整合入口 |
| `_persist_turn_pos()` | 同时持久化 turn_pos（来自 scheduler）和 read_index（来自 store） |
| `_publish_room_status()` | 需读取 scheduler.state + turn_pos + store.messages |
| `_append_message()` | 调 store.append + DB 写 + bus 发布 + `_update_turn_state_on_message` |

**委托给子组件的方法（ChatRoom 保留同名属性/方法，内部委托）：**

```python
@property
def state(self):
    return self._scheduler.state

def get_current_turn_agent(self):
    agent_id = self._scheduler.get_current_turn_agent_id()
    return self._get_agent_by_id(agent_id)

@property
def messages(self):
    return self._store.messages
```

所有对外公共方法签名完全不变。

### 数据流示意

```
外部调用 add_message(sender_id, content)
  → ChatRoom._append_message()
      → RoomMessageStore.append(msg)           # 写消息
      → DB persist                              # 持久化
      → messageBus.publish()                   # 广播
      → ChatRoom._update_turn_state_on_message(sender_id)
          → RoomTurnScheduler.wake_up()        # 如果是 IDLE，唤醒
          → RoomTurnScheduler.mark_has_content()
          → RoomTurnScheduler.resolve_next_dispatchable_agent()
          → ChatRoom._publish_room_status()     # 广播调度事件

外部调用 finish_turn(agent_id)
  → ChatRoom.finish_turn()
      → RoomTurnScheduler.go_next_turn()       # 推进位置
      → ChatRoom._persist_turn_pos()
          → DB persist(scheduler.turn_pos, store.get_read_index())
      → RoomTurnScheduler.resolve_next_dispatchable_agent()
      → ChatRoom._publish_room_status()
```

### `__init__.py` 重导出

保证对所有调用方完全透明：

```python
# src/service/roomService/__init__.py
from .core import (
    startup, shutdown,
    load_team_rooms, load_all_rooms,
    close_team_rooms,
    restore_team_rooms_runtime_state, restore_all_rooms_runtime_state,
    get_room, get_room_by_key, get_all_rooms,
    get_rooms_for_agent, get_agent_names, get_room_messages_from_db,
    create_team_rooms, overwrite_team_rooms, overwrite_dept_rooms,
    batch_create_rooms, update_room_agents,
    activate_rooms,
    resolve_room_max_turns, ToolCallContext,
)
from .chatRoom import ChatRoom
```

---

## 迁移计划

### 阶段一：平行创建目录，保留原文件

1. 创建 `src/service/roomService/` 目录
2. 新建 `scheduler.py`：将 `RoomTurnScheduler` 从零编写（不从 `ChatRoom` 直接剪切），使用类型注解
3. 新建 `messageStore.py`：同上，编写 `RoomMessageStore`
4. 运行测试，确认新类逻辑正确（可先写 unit test）

### 阶段二：改造 ChatRoom

1. 修改 `ChatRoom.__init__`：持有 `_scheduler` 和 `_store`
2. 逐方法将内部 `self._turn_pos` / `self._state` 等直接访问替换为委托调用
3. 保持所有对外方法名和签名不变
4. 运行完整测试套件

### 阶段三：迁移模块级函数

1. 新建 `core.py`：将 L619-929 的函数原样移入
2. 在 `core.py` 顶部 import `ChatRoom` from `.chatRoom`
3. 运行完整测试套件

### 阶段四：创建 `__init__.py` 并删除原文件

1. 编写 `__init__.py` 重导出所有公共符号
2. 删除 `src/service/roomService.py`（此时 `roomService/` 目录取代它）
3. Python 的 `import service.roomService` 会自动找到 `roomService/__init__.py`，所有调用方无需改动
4. 运行完整测试套件（包括 API 测试）确认零回归

---

## 风险点

| 风险 | 说明 | 缓解措施 |
|------|------|---------|
| 跨组件状态同步遗漏 | `_update_turn_state_on_message` 修改多个状态，提取后可能漏调 | 按数据流示意图逐字段检查，重点测试 IDLE 唤醒场景 |
| `_persist_turn_pos` 读取两处状态 | 需同时读 scheduler.turn_pos 和 store.read_index | 明确在 ChatRoom 中协调，不下沉到子组件 |
| 循环导入 | `chatRoom.py` → `scheduler.py` / `messageStore.py`，`core.py` → `chatRoom.py` | 单向依赖：core → chatRoom → scheduler/messageStore，无循环 |
| `__init__.py` 遗漏重导出符号 | 导致 ImportError | 迁移完成后用 `python -c "import service.roomService; print(dir())"` 验证 |

## 测试策略

- 阶段一结束：`scheduler.py` 和 `messageStore.py` 配套 unit test
- 阶段二结束：运行 `tests/unit` + `tests/integration`，通过率须与迁移前相同
- 阶段四结束：补充运行 `tests/api`（需启动后端子进程），确认 API 行为不变
