# roomService 重构记录

> 完成时间：2026-04-26

## 背景

`src/service/roomService.py` 原为 929 行单文件模块，包含 `ChatRoom` 类和大量模块级函数，职责混杂、维护成本高。

## 已完成的改动

### 目录结构

```text
src/service/
├── roomService/             # 原 roomService.py → 拆分为包
│   ├── __init__.py          # 重导出所有公共符号，对外接口完全不变
│   ├── core.py              # 模块级函数 + 全局注册表（原 L619-929）
│   ├── chatRoom.py          # ChatRoom 类（含调度状态机、消息管理）
│   └── messageStore.py      # RoomMessageStore（消息缓冲 + 未读索引）
```

原 `roomService.py` 已删除，由同名包替代，`import service.roomService` 自动路由到 `__init__.py`，所有调用方零修改。

### `core.py` — 房间注册表与生命周期

将原模块级变量和函数（L619-929）迁入，无逻辑改动：

- 全局注册表：`_rooms`、`_rooms_by_id`
- 生命周期：`startup()`, `shutdown()`
- 加载与恢复：`load_team_rooms()`, `load_all_rooms()`, `restore_*()` 系列
- 查询：`get_room()`, `get_all_rooms()`, `get_agent_names()`, `get_rooms_for_agent()` 等
- CRUD 编排：`create_team_rooms()`, `overwrite_team_rooms()`, `batch_create_rooms()`, `update_room_agents()` 等
- 工具函数：`resolve_room_max_turns()`, `ToolCallContext` dataclass
- 顺带删除了两处从未被调用的死代码：`_same_speaker`、`_infer_room_type`

### `messageStore.py` — 消息缓冲与未读索引

提取 `RoomMessageStore` 类，管理：

- `_messages: List[GtCoreRoomMessage]` — 内存消息列表
- `_agent_read_index: Dict[int, int]` — 每个 agent 的已读指针

公共接口：`append()`, `get_unread()`, `mark_all_read()`, `inject()`, `export_read_index()`

### ChatRoom 类简化

在拆包的同时，对 `ChatRoom` 做了一系列方法简化：

| 改动 | 说明 |
|------|------|
| 删除 `_get_agent_by_id` | 与 `get_gt_agent` 逻辑完全重复，合并 |
| 删除 `agents` / `agent_names` 属性 | 合并为 `get_agent_ids(include_special=False)` |
| 删除 `_get_agent_display_name` | i18n 逻辑移入 `GtAgent.display_name` 属性 |
| `get_agent_name` → `_display_name_of` | 改为私有方法，从公共 API 移除 |
| `mark_has_content` / `mark_no_content` | 合并为单参数方法 `mark_content(has_content)` |
| 修正 `sender_name` 注释 | 明确标注为 i18n display_name（供 LLM prompt 和日志），非稳定标识名 |

### GtAgent 新增 `display_name` 属性

```python
@property
def display_name(self) -> str:
    """从 i18n 配置解析显示名，fallback 到 agent.name。"""
    return i18nUtil.extract_i18n_str(
        self.i18n.get("display_name") if self.i18n else None,
        default=self.name,
    ) or self.name
```

符合四层架构（model → util 依赖允许）。将 i18n 解析职责集中到数据模型，不再散落在 ChatRoom 中。

## 非目标（未实施）

- **`RoomTurnScheduler` 提取**：轮次调度状态机仍保留在 `ChatRoom` 内，评估后改动收益不足以抵消风险，暂缓。
- **N+1 查询优化**（roomController.py）：独立评估，未在本次处理。
- **DAL / API 层变更**：零修改。

## 测试结果

全量 500 个测试（unit + integration）通过，零回归。

