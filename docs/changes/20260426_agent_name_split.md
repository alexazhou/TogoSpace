# Agent Name 职责拆分设计

> 创建时间：2026-04-26

## 背景

当前 `ChatRoom` 和 `roomService` 中包含多个与 Agent name 相关的方法，职责边界模糊：

- `ChatRoom._display_name_of(agent_id)` — 获取显示名称（i18n）
- `ChatRoom._get_agent_stable_name(agent_id)` — 获取稳定标识名
- `ChatRoom.get_agent_id_by_name(name)` — 根据名称获取 agent_id
- `ChatRoom.get_current_turn_agent_name()` — 当前发言人名称
- `roomService.get_agent_names(room_id)` — 外部 API

同时 `ToolCallContext` 使用 `agent_name` 字段，导致工具函数频繁调用 `get_agent_id_by_name()` 做转换。

## 目标

1. **roomService 只关心 agent_id** — 房间调度逻辑不应涉及 name 解析
2. **agentService 承载 name 相关功能** — 统一 agent 元信息查询入口
3. **ToolCallContext 改用 agent_id** — 减少运行时 name→id 转换
4. **消息结构去掉 sender_name** — 改用 sender_i18n，让消费方自行解析显示名

## 约束

**循环依赖约束**：`roomService` 不能 `import agentService`，否则产生循环依赖。

因此：
- ChatRoom 内部日志可继续使用 `_display_name_of()`（查询内部 `_agents`，不依赖 agentService）
- agentService 提供 name 相关方法，供 controller、funcToolService 等上层调用
- 对外 API（如 `get_agent_id_by_name`）移除，上层改用 agentService

## 设计方案

### 1. GtCoreRoomMessage 结构变更

去掉 `sender_name`，保留 `sender_i18n`：

```python
# model/coreModel/gtCoreChatModel.py

@dataclass
class GtCoreRoomMessage:
    """房间消息数据类"""
    sender_id: int  # 发送者 agent_id
    sender_i18n: dict = field(default_factory=dict)  # 发送者 i18n 信息，含 display_name
    content: str
    send_time: datetime
```

消费方（promptBuilder）从 `sender_i18n` 解析显示名：

```python
# agentService/promptBuilder.py

def format_room_message(room_name: str, sender_id: int, sender_i18n: dict, content: str, lang: str) -> str:
    """格式化房间消息，从 i18n 解析显示名。"""
    sender_name = i18nUtil.extract_i18n_str(
        sender_i18n.get("display_name"),
        default=str(sender_id),
        lang=lang,
    ) or str(sender_id)
    sender_label = "系统提醒" if SpecialAgent.value_of(sender_id) == SpecialAgent.SYSTEM else sender_name
    return f"【房间《{room_name}》】【{sender_label}】： {content}"
```

### 2. agentService 新增方法

```python
# agentService/core.py

def get_gt_agent_by_id(agent_id: int) -> GtAgent | None:
    """根据 agent_id 获取 GtAgent。"""
    agent = _agents.get(agent_id)
    return agent.gt_agent if agent is not None else None

def get_agent_display_name(agent_id: int, lang: str | None = None) -> str:
    """获取 Agent 显示名称（i18n）。"""
    agent = _agents.get(agent_id)
    if agent is not None:
        return agent.gt_agent.display_name
    special = SpecialAgent.value_of(agent_id)
    return special.name if special is not None else str(agent_id)

def get_agent_stable_name(agent_id: int) -> str:
    """获取 Agent 稳定标识名。"""
    agent = _agents.get(agent_id)
    if agent is not None:
        return agent.gt_agent.name
    special = SpecialAgent.value_of(agent_id)
    return special.name if special is not None else str(agent_id)

def get_agent_id_by_stable_name(team_id: int, name: str) -> int | None:
    """根据 Agent 名称获取 agent_id（team 级别查找）。"""
    special_agent = SpecialAgent.value_of(name)
    if special_agent is not None:
        return int(special_agent.value)
    for agent_id, agent in _agents.items():
        if agent.gt_agent.team_id == team_id and agent.gt_agent.name == name:
            return agent_id
    return None

def get_agent_i18n(agent_id: int) -> dict:
    """获取 Agent i18n 配置（用于消息构建）。"""
    agent = _agents.get(agent_id)
    if agent is not None:
        return agent.gt_agent.i18n or {}
    return {}
```

### 3. ChatRoom 改造（不依赖 agentService）

**保留的方法（内部使用）**：
- `_get_agent_stable_name(agent_id)` — 保留，用于 `export_agent_read_index` 持久化 key（查询内部 `_agents`，无循环依赖）

**移除的方法**：
- `_display_name_of(agent_id)` → 删除，日志改用纯 agent_id
- `get_agent_id_by_name(name)` → 删除（上层改用 agentService）
- `get_current_turn_agent_name()` → 删除（改用 agent_id）
- `get_gt_agent(agent_id)` → 删除（上层改用 agentService）

**内部改造**：
- `_append_message()` 改为设置 `sender_i18n`（从内部 `_agents` 查询），去掉 `sender_name`
- 日志输出改为纯 agent_id：`f"收到来自 agent_id={sender_id} 的消息"`
- `export_agent_read_index()` 继续使用 `_get_agent_stable_name()` 作为 key（兼容已有数据）
- `format_log()` 改为输出 agent_id
- `_build_current_turn_agent_dict()` 改为返回 agent_id + i18n

**说明**：ChatRoom 构造时持有 `_agents: List[GtAgent]`，`_get_agent_stable_name` 直接查询此列表，不产生循环依赖。

### 4. export_agent_read_index 保持不变

继续使用 `stable_name` 作为持久化 key，无需迁移已有数据：

```python
def export_agent_read_index(self) -> Dict[str, int]:
    return {
        self._get_agent_stable_name(aid): idx
        for aid, idx in self._store.get_read_index().items()
    }
```

### 5. ToolCallContext 改造

```python
@dataclass
class ToolCallContext:
    """工具调用时注入的上下文。"""
    agent_id: int        # 原 agent_name 改为 agent_id
    team_id: int
    chat_room: ChatRoom
    tool_name: str = ""
```

### 6. roomService 导出清理

`roomService/__init__.py` 移除：
- `get_agent_names` 导出

### 7. ChatRoom 保留的内部数据

ChatRoom 仍持有 `_agents: List[GtAgent]` 用于：
- 消息创建时获取 `sender_i18n`
- `build_initial_system_message()` 获取 `agent.display_name`

这些都是查询内部数据，不需要 import agentService。

## 影响范围

### 需修改文件

| 文件 | 改动类型 |
|------|----------|
| `src/model/coreModel/gtCoreChatModel.py` | 去掉 `sender_name` 字段 |
| `src/service/agentService/core.py` | 新增 5 个方法 |
| `src/service/agentService/__init__.py` | 新增导出 |
| `src/service/agentService/promptBuilder.py` | 改为从 sender_i18n 解析显示名 |
| `src/service/agentService/agentTurnRunner.py` | 适配新消息结构 |
| `src/service/roomService/chatRoom.py` | 移除 5 个方法，日志改用 agent_id |
| `src/service/roomService/core.py` | 移除 `get_agent_names`，改造 `ToolCallContext`，日志改用 agent_id |
| `src/service/roomService/__init__.py` | 移除导出 |
| `src/service/funcToolService/tools.py` | 改用 `agent_id` |

### 不需修改

- GtAgent 模型（已有 `display_name` 属性）
- `gtRoomManager`（read_index 格式保持不变）
- controller 层 API 响应结构（独立评估）

## 实施步骤

### 步骤 1：GtCoreRoomMessage 去掉 sender_name

1. 删除 `sender_name` 字段
2. 保留 `sender_i18n`，添加 default_factory

### 步骤 2：agentService 新增方法

在 `core.py` 添加 5 个新方法，并在 `__init__.py` 导出。

### 步骤 3：ChatRoom 内部改造（不依赖 agentService）

1. `_append_message()` 改为设置 `sender_i18n`（从内部 `_agents` 查询）
2. 日志输出改为纯 agent_id
3. `export_agent_read_index()` 保持不变（继续使用 `_get_agent_stable_name`）
4. 保留 `_get_agent_stable_name`
5. 删除 `_display_name_of` / `get_agent_id_by_name` / `get_current_turn_agent_name` / `get_gt_agent`

### 步骤 4：promptBuilder 适配新消息结构

`build_turn_begin_prompt_from_messages()` 改为从 `sender_i18n` 解析显示名。

### 步骤 5：ToolCallContext 改造

将 `agent_name` 改为 `agent_id`，同步修改所有使用 `_context.agent_name` 的地方。

### 步骤 6：移除 roomService 导出

删除 `get_agent_names` 函数和导出。

### 步骤 7：测试验证

运行全量测试，确保零回归。

## 验收标准

1. 后端启动正常
2. `./scripts/run_tests.sh` 全量测试通过
3. `rg "sender_name" src/model/coreModel/gtCoreChatModel.py` 无结果（字段已删除）
4. `rg "get_agent_id_by_name|_display_name_of|get_current_turn_agent_name|get_gt_agent" src/service/roomService/` 无结果（`_get_agent_stable_name` 除外）
5. `rg "ToolCallContext.*agent_name" src/service/roomService/core.py` 无结果
6. agentService 新方法可正常调用