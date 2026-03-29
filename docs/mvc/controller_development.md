# Controller 开发指南

本文档总结了 TeamAgent 项目中 HTTP Controller 的开发规范和最佳实践。

## 目录

- [数据输入处理](#数据输入处理)
- [数据输出序列化](#数据输出序列化)
- [断言和验证](#断言和验证)
- [错误处理](#错误处理)
- [Controller 与 DAL](#controller-与-dal)
- [URL 定义规范](#url-定义规范)
- [路由注册](#路由注册)
- [完整示例](#完整示例)

---

## 数据输入处理

### 使用 `parse_request` 方法统一解析

所有需要解析请求体的 POST/PUT 请求，使用 `BaseHandler.parse_request()` 方法：

```python
# ✅ 推荐 - 使用 parse_request
async def post(self, team_id_str: str) -> None:
    request = self.parse_request(CreateRoomRequest)
    # 使用 request.name, request.type 等

# ❌ 不推荐 - 手动解析
async def post(self, team_id_str: str) -> None:
    body = json.loads(self.request.body)
    request = CreateRoomRequest(**body)
```

### 定义请求模型

使用 Pydantic BaseModel 定义请求数据结构：

```python
from pydantic import BaseModel

class CreateRoomRequest(BaseModel):
    name: str
    type: str
    initial_topic: str | None = None
    max_turns: int = 100

class UpdateRoomRequest(BaseModel):
    type: str
    initial_topic: str | None = None
    max_turns: int | None = None
```

### 路径参数

路径参数直接作为方法参数获取：

```python
async def get(self, team_id_str: str, room_id_str: str) -> None:
    # team_id_str 和 room_id_str 来自 URL 路径
    # /teams/{team_id}/rooms/{room_id}.json
```

### 优先使用 DB 对象

在定义请求/响应模型或处理数据时，应尽量复用 DB 层已有的模型对象（`GtXxx`），而不是新建一个字段几乎完全相同但名字不同的 DTO/数据对象。

- **请求模型**：如果 API 的输入字段与 `GtXxx` 高度重合，应考虑直接接收或内部转换为 `GtXxx`。
- **避免冗余**：不要创建类似 `AgentSaveDTO` 这样与 `GtAgent` 职责重叠的中间类。
- **一致性**：统一使用 `GtXxx` 有助于减少不同层级间的转换成本，并使代码库的领域模型更加清晰。
- **Service 编排**：Controller 仅负责简单的到 `GtXxx` 的映射，复杂的对象转换、数据补全或逻辑编排应在 Service 层完成。

---

## 数据输出序列化

### 使用 `return_json` 方法统一返回

所有 JSON 响应使用 `BaseHandler.return_json()` 方法：

```python
# 返回字典
self.return_json({"status": "created", "name": room_name})

# 返回 Pydantic 模型（自动处理 datetime 等类型）
self.return_json(RoomInfo(name="test", type="group"))

# 返回 DbModelBase 实例（自动转换为字典）
self.return_json(team)

# 返回列表
self.return_json({"rooms": rooms})
```

### 自动类型转换

`return_json` 会自动处理以下类型：

| 类型 | 处理方式 |
|------|----------|
| `BaseModel` | `model_dump(mode="json")` |
| `DbModelBase` | 转换为字典 |
| `Enum` | 转换为 `.name` |
| `datetime` | 转换为 ISO 字符串 |
| `list` / `dict` | JSON 序列化 |

---

## 断言和验证

### 使用 `assertUtil` 进行验证

所有业务逻辑验证使用 `util.assertUtil` 中的断言函数：

```python
from util import assertUtil

# 检查条件为真
assertUtil.assertTrue(
    exists,
    error_message=f"Team ID '{team_id}' not found",
    error_code="team_not_found"
)

# 检查对象非空
assertUtil.assertNotNull(
    room,
    error_message=f"Room ID '{room_id}' not found",
    error_code="room_not_found"
)

# 检查相等
assertUtil.assertEqual(
    existing, None,
    error_message=f"Room '{request.name}' already exists",
    error_code="room_exists"
)
```

### 断言失败行为

断言失败时会抛出 `TeamAgentException`，`BaseHandler` 会自动捕获并返回：

```json
{
  "error_code": "team_not_found",
  "error_desc": "Team ID '1' not found"
}
```

### 常见问题

#### assertNotNull 后不需要冗余判断

`assertNotNull` 失败会抛出异常，后续代码不会执行，不需要额外判断：

```python
# ✅ 推荐
team = await gtTeamManager.get_team_by_id(team_id)
assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")
# 直接继续业务逻辑

# ❌ 不推荐 - 冗余判断
team = await gtTeamManager.get_team_by_id(team_id)
assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")
if team is None:
    return  # 永远不会执行
```

#### 批量操作先检查所有数据存在性

批量更新时，先用一条 in 查询检查所有 id 是否存在，避免更新到一半失败：

```python
# ✅ 推荐 - 先检查，再更新
agent_ids = [item.id for item in request.agents]
existing_agents = await gtAgentManager.get_agents_by_ids(agent_ids)
assertUtil.assertEqual(len(existing_agents), len(agent_ids), f"input {len(agent_ids)} agent ids, but only found {len(existing_agents)} existed")

for item in request.agents:
    await gtAgentManager.update_agent(...)

# ❌ 不推荐 - 边检查边更新，可能中途失败
for item in request.agents:
    agent = await gtAgentManager.get_agent_by_id(item.id)
    if agent is None:
        raise TeamAgentException(...)  # 已更新的数据无法回滚
    await gtAgentManager.update_agent(...)
```

#### assert 语句保持简洁

单行 assert，不需要过多换行：

```python
# ✅ 推荐
assertUtil.assertEqual(len(existing_agents), len(agent_ids), f"input {len(agent_ids)} agent ids, but only found {len(existing_agents)} existed")

# ❌ 不推荐 - 过多换行
assertUtil.assertEqual(
    len(existing_agents),
    len(agent_ids),
    error_message=f"Agent IDs not found: expected {len(agent_ids)}, got {len(existing_agents)}",
    error_code="agent_update_failed",
)
```

---

## 错误处理

### 不需要 try-catch

Controller 中**不需要**手动捕获异常：

```python
# ✅ 推荐 - 直接抛出异常
async def post(self, team_id_str: str) -> None:
    team_id = int(team_id_str)
    team = await gtTeamManager.get_team_by_id(team_id)
    assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")
    # 业务逻辑...

# ❌ 不推荐 - 手动捕获
async def post(self, team_id_str: str) -> None:
    try:
        team_id = int(team_id_str)
        team = await gtTeamManager.get_team_by_id(team_id)
        if team is None:
            self.return_with_error("team_not_found", "Team not found")
            return
        # 业务逻辑...
    except Exception as e:
        # ...
```

### 自定义异常

如果需要抛出自定义异常，使用 `TeamAgentException`：

```python
from exception import TeamAgentException

async def post(self) -> None:
    if some_condition:
        raise TeamAgentException("Invalid input", "invalid_request")
```

---

## Controller 与 DAL

### 默认规则

- 默认优先通过 service 封装业务流程，controller 负责参数解析、断言和响应拼装。

### 允许直连 DAL 的情况

满足以下条件时，controller 可以直接调用 DAL Manager：

- 单表或单领域对象的简单 CRUD
- 不涉及运行时状态同步
- 不涉及消息广播或调度器联动
- 不涉及跨 service 编排
- 不需要额外业务规则收敛

示例：`roleTemplateController -> gtRoleTemplateManager`

### 不允许直连 DAL 的情况

以下场景仍应通过 service 层：

- 热更新、重建运行时对象
- 跨表事务或多步编排
- 持久化恢复流程
- 需要统一复用的业务校验

---

## URL 定义规范

### 命名规则

| 资源类型 | URL 格式 | 示例 |
|----------|----------|------|
| 列表 | `/{资源}/list.{扩展名}` | `/teams/list.json` |
| 详情 | `/{资源}/{id}.{扩展名}` | `/teams/1.json` |
| 创建 | `/{资源}/create.{扩展名}` | `/teams/create.json` |
| 修改 | `/{资源}/{id}/modify.{扩展名}` | `/teams/1/modify.json` |
| 删除 | `/{资源}/{id}/delete.{扩展名}` | `/teams/1/delete.json` |
| 子资源列表 | `/{父资源}/{父id}/{子资源}/list.{扩展名}` | `/teams/1/rooms/list.json` |
| 子资源详情 | `/{父资源}/{父id}/{子资源}/{子id}.{扩展名}` | `/teams/1/rooms/2.json` |

### HTTP 方法约定

| 操作 | HTTP 方法 | 说明 |
|------|-----------|------|
| 查询 | `GET` | 获取数据 |
| 创建/修改/删除 | `POST` | 绝大多数写操作使用 POST（简化调用） |
| 特殊更新 | `PUT` | 少量场景会使用 PUT（例如部门主管变更） |

### Handler 命名规范

List Handler 只用于查询，写操作使用单独的 Handler：

| Handler 命名 | 用途 | 示例 |
|--------------|------|------|
| `XxxListHandler` | 仅 GET，查询列表 | `AgentListHandler` |
| `XxxBatchUpdateHandler` | PUT/POST，批量更新 | `AgentBatchUpdateHandler` |
| `XxxCreateHandler` | POST，创建资源 | `TeamCreateHandler` |
| `XxxDetailHandler` | GET，获取详情 | `AgentDetailHandler` |
| `XxxUpdateHandler` | PUT/POST，更新资源 | `DeptTreeUpdateHandler` |
| `XxxModifyHandler` | POST，修改资源 | `TeamModifyHandler` |
| `XxxDeleteHandler` | POST，删除资源 | `TeamDeleteHandler` |

```python
# ✅ 推荐 - 查询和更新分开
class AgentListHandler(BaseHandler):
    async def get(self):  # 仅 GET
        ...

class AgentBatchUpdateHandler(BaseHandler):
    async def put(self):  # 单独 Handler
        ...

# ❌ 不推荐 - List Handler 包含写操作
class AgentListHandler(BaseHandler):
    async def get(self):
        ...

    async def put(self):  # 容易混淆
        ...
```

### 单资源查询与更新分离

对于单个资源的查询和更新，应使用不同的 Handler 和带动词的 URL：

| 操作 | URL 格式 | Handler 命名 |
|------|----------|--------------|
| 查询 | `/{资源}/{id}.json` | `XxxDetailHandler` |
| 更新 | `/{资源}/{id}/update.json` | `XxxUpdateHandler` |
| 修改 | `/{资源}/{id}/modify.json` | `XxxModifyHandler` |
| 删除 | `/{资源}/{id}/delete.json` | `XxxDeleteHandler` |

```python
# ✅ 推荐 - 查询和更新使用不同的 Handler
class DeptTreeDetailHandler(BaseHandler):
    async def get(self, team_id_str: str):  # GET /teams/1/dept_tree.json
        ...

class DeptTreeUpdateHandler(BaseHandler):
    async def put(self, team_id_str: str):  # PUT /teams/1/dept_tree/update.json
        ...

# ❌ 不推荐 - 同一个 Handler 同时处理查询和更新
class DeptTreeHandler(BaseHandler):
    async def get(self, team_id_str: str):
        ...

    async def put(self, team_id_str: str):  # 容易混淆
        ...
```

---

## 路由注册

### 在 `route.py` 中注册路由

```python
import tornado.web
from controller import roleTemplateController, agentController, roomController, wsController, teamController, deptController

application = tornado.web.Application([
    # Role templates
    (r"/role_templates/list.json",                   roleTemplateController.RoleTemplateListHandler),
    (r"/role_templates/([^/]+).json",               roleTemplateController.RoleTemplateDetailHandler),

    # Agents (运行时成员)
    (r"/agents/list.json",                          agentController.AgentListHandler),
    (r"/teams/(\d+)/agents/batch_update.json",      agentController.AgentBatchUpdateHandler),
    (r"/teams/(\d+)/agents/([^/]+).json",           agentController.AgentDetailHandler),

    # Room (运行时)
    (r"/rooms/list.json",                           roomController.RoomListHandler),
    (r"/rooms/(\d+)/messages/list.json",            roomController.RoomMessagesHandler),
    (r"/rooms/(\d+)/messages/send.json",            roomController.RoomMessagesHandler),

    # WebSocket
    (r"/ws/events.json",                            wsController.EventsWsHandler),

    # Team
    (r"/teams/list.json",                   teamController.TeamListHandler),
    (r"/teams/create.json",                 teamController.TeamCreateHandler),
    (r"/teams/(\d+).json",                  teamController.TeamDetailHandler),
    (r"/teams/(\d+)/modify.json",           teamController.TeamModifyHandler),
    (r"/teams/(\d+)/delete.json",           teamController.TeamDeleteHandler),

    # Team Rooms
    (r"/teams/(\d+)/rooms/list.json",               roomController.TeamRoomsHandler),
    (r"/teams/(\d+)/rooms/create.json",             roomController.TeamRoomCreateHandler),
    (r"/teams/(\d+)/rooms/(\d+).json",              roomController.TeamRoomDetailHandler),
    (r"/teams/(\d+)/rooms/(\d+)/modify.json",       roomController.TeamRoomModifyHandler),
    (r"/teams/(\d+)/rooms/(\d+)/delete.json",       roomController.TeamRoomDeleteHandler),
    (r"/teams/(\d+)/rooms/(\d+)/agents/list.json",  roomController.TeamRoomMembersHandler),
    (r"/teams/(\d+)/rooms/(\d+)/agents/modify.json",roomController.TeamRoomMembersModifyHandler),

    # Dept Tree (V10)
    (r"/teams/(\d+)/dept_tree.json",                deptController.DeptTreeDetailHandler),
    (r"/teams/(\d+)/dept_tree/update.json",         deptController.DeptTreeUpdateHandler),
], **tornado_settings)
```

### 路由参数

使用 `(\d+)` 匹配数值 ID，`([^/]+)` 匹配字符串参数，参数按顺序传递给 handler 方法：

```python
# URL: /teams/1/rooms/2.json
# 路由: (r"/teams/(\d+)/rooms/(\d+).json", Handler)

async def get(self, team_id_str: str, room_id_str: str) -> None:
    team_id = int(team_id_str)  # 1
    room_id = int(room_id_str)  # 2
```

---

## 完整示例

### 示例：Team 房间管理 Controller

```python
# controller/roomController.py
from typing import List
from pydantic import BaseModel
from controller.baseController import BaseHandler
from dal.db import gtTeamManager, gtRoomManager
from service import teamService
from util import assertUtil
from util.configTypes import TeamRoomConfig

# 请求模型
class CreateRoomRequest(BaseModel):
    name: str
    type: str
    initial_topic: str | None = None
    max_turns: int = 100

class UpdateRoomRequest(BaseModel):
    type: str
    initial_topic: str | None = None
    max_turns: int | None = None

class UpdateMembersRequest(BaseModel):
    members: list[str]

# Handler: 获取 Team 下的所有 Room
class TeamRoomsHandler(BaseHandler):
    async def get(self, team_id_str: str) -> None:
        team_id = int(team_id_str)
        team = await gtTeamManager.get_team_by_id(team_id)
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")

        # 获取房间列表
        rooms = await gtRoomManager.get_rooms_by_team(team_id)
        self.return_json({"rooms": rooms})

# Handler: 创建 Room
class TeamRoomCreateHandler(BaseHandler):
    async def post(self, team_id_str: str) -> None:
        # 解析请求
        request = self.parse_request(CreateRoomRequest)
        team_id = int(team_id_str)

        # 验证
        team = await gtTeamManager.get_team_by_id(team_id)
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")

        existing_rooms = await gtRoomManager.get_rooms_by_team(team_id)
        existing = next((r for r in existing_rooms if r.name == request.name), None)
        assertUtil.assertEqual(existing, None, error_message=f"Room '{request.name}' already exists", error_code="room_exists")

        # 业务逻辑
        new_room = TeamRoomConfig(
            name=request.name,
            members=[],
            initial_topic=request.initial_topic or "",
            max_turns=request.max_turns,
        )
        # 真实实现里通常需要把 existing_rooms 合并后再 upsert，避免覆盖其它房间
        room_configs: List[TeamRoomConfig] = [
            TeamRoomConfig(
                name=r.name,
                members=await gtRoomManager.get_members_by_room(r.id),
                initial_topic=r.initial_topic,
                max_turns=r.max_turns,
            )
            for r in existing_rooms
        ]
        room_configs.append(new_room)

        await gtRoomManager.upsert_rooms(team_id, room_configs)
        await teamService.hot_reload_team(team.name)

        # 返回
        self.return_json({"status": "created", "room_name": request.name})
```

---

## 快速检查清单

在编写或审查 Controller 代码时，确认以下事项：

- [ ] 使用 `parse_request` 解析请求体
- [ ] 使用 Pydantic BaseModel 定义请求/响应模型
- [ ] 使用 `assertUtil` 进行验证
- [ ] 使用 `return_json` 返回响应
- [ ] 不手动捕获异常（除非特殊场景）
- [ ] URL 符合命名规范
- [ ] 在 `route.py` 中注册路由
- [ ] 修改/删除操作使用 `POST` 方法
