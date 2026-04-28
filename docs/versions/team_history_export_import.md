# 团队历史记录导入/导出 — 第一阶段设计方案

## 1. 范围

仅导出/导入团队的**运行历史数据**，不含团队配置（成员、房间结构等）：

| 数据 | 来源表 | 说明 |
|------|--------|------|
| `room_messages` | `GtRoomMessage` | 所有房间的聊天记录（含 Operator 发言） |
| `room_states` | `GtRoom.agent_read_index` + `turn_pos` | 房间运行时状态（已读游标 + 轮次位置） |
| `agent_histories` | `GtAgentHistory` | Agent LLM 对话历史（保证 Agent 可继续运行） |
| `agent_activities` | `GtAgentActivity` | Agent 活动记录 |
| `agent_tasks` | `GtAgentTask` | Agent 任务队列（保证 Agent 可从断点恢复） |

**不包含**：`agent_histories` 中的 COMPACT_SUMMARY 以外的大体积内容不做特殊裁剪，全量导出。

---

## 2. 导出文件格式（`.tagx` / JSON）

```json
{
  "format_version": "1",
  "exported_at": "2026-04-21T10:00:00+00:00",
  "team_name": "软件研发团队",

  "room_messages": [
    {
      "room_name": "主会议室",
      "agent_name": "小马哥",
      "content": "我们来讨论一下需求",
      "send_time": "2026-04-20 09:00:00"
    },
    {
      "room_name": "主会议室",
      "agent_name": "OPERATOR",
      "content": "好的，开始吧",
      "send_time": "2026-04-20 09:00:05"
    }
  ],

  "room_states": {
    "主会议室": {
      "turn_pos": 3,
      "agent_read_index": {
        "小马哥": 42,
        "OPERATOR": 42
      }
    }
  },

  "agent_histories": [
    {
      "agent_name": "小马哥",
      "seq": 1,
      "role": "user",
      "tool_call_id": null,
      "message": { "role": "user", "content": "你好" },
      "status": "SUCCESS",
      "error_message": null,
      "tags": [],
      "usage": null
    }
  ],

  "agent_activities": [
    {
      "agent_name": "小马哥",
      "activity_type": "LLM_INFER",
      "status": "SUCCEEDED",
      "title": "Turn 1 · 主会议室",
      "detail": "",
      "error_message": null,
      "started_at": "2026-04-20T09:00:00",
      "finished_at": "2026-04-20T09:00:05",
      "duration_ms": 5000,
      "metadata": {}
    }
  ],

  "agent_tasks": [
    {
      "agent_name": "小马哥",
      "task_type": "ROOM_MESSAGE",
      "task_data": { "room_name": "主会议室" },
      "status": "PENDING",
      "error_message": null
    }
  ]
}
```

### ID 序列化规则

| 字段 | 导出处理 | 导入处理 |
|------|----------|----------|
| `room_message.agent_id` | `SpecialAgent`（-1/-2）→ 枚举 name；普通 → agent name | 反查 name → id |
| `room_states` 的 key（`str(agent_id)`） | → agent name（含 SpecialAgent） | 反查 name → `str(new_agent_id)` |
| `room_states` 的 value（位置序号） | 原样保留，**不转换** | 原样写入 |
| `agent_history.agent_id` | → agent name | 反查 name → id |
| `agent_activity.agent_id` | → agent name | 反查 name → id |
| `agent_task.agent_id` | → agent name | 反查 name → id |
| `agent_task.task_data.room_id` | → room name | 反查 name → id |

> `agent_read_index` 的 value 已是消息列表的位置序号（`len(messages)` 写入），不是数据库消息 ID，因此跨实例导入无需转换。

---

## 3. API

```
GET  /teams/{team_id}/export/history.json
     响应头: Content-Disposition: attachment; filename="team_{name}_history.tagx"
     响应体: TeamHistoryExport JSON

POST /teams/{team_id}/import/history.json
     请求体: TeamHistoryExport JSON（即导出文件内容）
     响应体:
     {
       "status": "ok",
       "imported": {
         "messages":    120,
         "histories":   300,
         "activities":  45,
         "tasks":       2
       },
       "skipped": {
         "messages":    ["未知房间: xxx"],
         "histories":   [],
         "activities":  ["未知成员: yyy"],
         "tasks":       []
       }
     }
```

---

## 4. 导入行为（覆盖模式）

```
1. 校验 team 存在
2. 停止 team 运行时（stop_team_runtime）
3. 清空数据（按依赖顺序）：
   agent_tasks → agent_histories → room_messages → agent_activities
   重置 agent_read_index（reset_room_read_index）
4. 构建映射表：
   room_name → room_id
   agent_name → agent_id（含 SpecialAgent）
5. 插入 room_messages（跳过找不到 room/agent 的条目）
6. 更新 room_states：
   key: agent_name → str(new_agent_id)；value 原样
   写入 turn_pos
7. 插入 agent_histories（保留原 seq，跳过找不到 agent 的条目）
8. 插入 agent_activities（跳过找不到 agent 的条目）
9. 插入 agent_tasks（task_data.room_name → room_id，跳过找不到的条目）
10. hot_reload_team 恢复运行时
11. 返回 summary
```

---

## 5. 文件改动清单

### 新增文件

| 文件 | 说明 |
|------|------|
| `src/model/coreModel/teamDataModel.py` | 导出格式的 Pydantic 模型（各 Record 类 + `TeamHistoryExport`） |
| `src/service/teamDataService.py` | `export_team_history` / `import_team_history` |
| `src/controller/teamDataController.py` | `TeamHistoryExportHandler` / `TeamHistoryImportHandler` |

### 修改文件

| 文件 | 改动 |
|------|------|
| `src/dal/db/gtAgentActivityManager.py` | 新增 `get_all_activities_by_team`、`delete_activities_by_team` |
| `src/dal/db/gtAgentHistoryManager.py` | 新增 `get_all_histories_by_team` |
| `src/dal/db/gtAgentTaskManager.py` | 新增 `get_all_tasks_by_team` |
| `src/route.py` | 注册 2 条新路由，import teamDataController |

---

## 6. 各模块详细设计

### 6.1 `teamDataModel.py`

```python
class RoomMessageRecord(BaseModel):
    room_name: str
    agent_name: str
    content: str
    send_time: str

class RoomStateRecord(BaseModel):
    turn_pos: int
    agent_read_index: dict[str, int]   # key: agent_name, value: 位置序号

class AgentHistoryRecord(BaseModel):
    agent_name: str
    seq: int
    role: str
    tool_call_id: str | None
    message: dict | None               # OpenAI message 原始 JSON
    status: str
    error_message: str | None
    tags: list[str]
    usage: dict | None

class AgentActivityRecord(BaseModel):
    agent_name: str
    activity_type: str
    status: str
    title: str
    detail: str
    error_message: str | None
    started_at: str
    finished_at: str | None
    duration_ms: int | None
    metadata: dict

class AgentTaskRecord(BaseModel):
    agent_name: str
    task_type: str
    task_data: dict                    # room_id 已替换为 room_name
    status: str
    error_message: str | None

class TeamHistoryExport(BaseModel):
    format_version: str = "1"
    exported_at: str
    team_name: str
    room_messages: list[RoomMessageRecord]
    room_states: dict[str, RoomStateRecord]   # key: room_name
    agent_histories: list[AgentHistoryRecord]
    agent_activities: list[AgentActivityRecord]
    agent_tasks: list[AgentTaskRecord]
```

### 6.2 DAL 新增函数

```python
# gtAgentActivityManager.py
async def get_all_activities_by_team(team_id: int) -> list[GtAgentActivity]:
    """全量查询，按 id asc，用于导出。"""

async def delete_activities_by_team(team_id: int) -> int:
    """删除 team 下所有活动记录，返回删除数量。"""

# gtAgentHistoryManager.py
async def get_all_histories_by_team(team_id: int) -> list[GtAgentHistory]:
    """全量查询所有 agent 的历史记录，按 agent_id, seq asc。"""

# gtAgentTaskManager.py
async def get_all_tasks_by_team(team_id: int) -> list[GtAgentTask]:
    """全量查询 team 下所有 agent 的任务记录。"""
```

### 6.3 `teamDataService.py` 关键逻辑

**导出辅助函数：**

```python
def _agent_id_to_name(agent_id: int, id_to_name: dict[int, str]) -> str:
    special = SpecialAgent.value_of(agent_id)
    if special is not None:
        return special.name
    return id_to_name.get(agent_id, str(agent_id))
```

**导入辅助函数：**

```python
def _agent_name_to_id(name: str, name_to_id: dict[str, int]) -> int | None:
    special = SpecialAgent.value_of(name)
    if special is not None:
        return special.value
    return name_to_id.get(name)
```

**`room_states` 导出：**
- 从 `GtRoom.agent_read_index`（key 为 `str(agent_id)`）转换 key → agent_name
- value（位置序号）原样保留

**`room_states` 导入：**
- key agent_name → `str(new_agent_id)`
- value 原样，调用 `gtRoomManager.update_room_state`

**`agent_task.task_data` 导出：**
- 若含 `room_id`，替换为 `room_name`（删除 `room_id` key，写入 `room_name`）

**`agent_task.task_data` 导入：**
- 若含 `room_name`，替换为 `room_id`（删除 `room_name` key，写入 `room_id`）

### 6.4 `route.py` 新增路由

```python
# Team 历史导入/导出
(r"/teams/(\d+)/export/history.json", teamDataController.TeamHistoryExportHandler),
(r"/teams/(\d+)/import/history.json", teamDataController.TeamHistoryImportHandler),
```
