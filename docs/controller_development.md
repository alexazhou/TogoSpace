# Controller 开发指南

本文档总结了 TeamAgent 项目中 HTTP Controller 的开发规范和最佳实践。

## 目录

- [数据输入处理](#数据输入处理)
- [数据输出序列化](#数据输出序列化)
- [断言和验证](#断言和验证)
- [错误处理](#错误处理)
- [URL 定义规范](#url-定义规范)
- [路由注册](#路由注册)
- [完整示例](#完整示例)

---

## 数据输入处理

### 使用 `parse_request` 方法统一解析

所有需要解析请求体的 POST/PUT 请求，使用 `BaseHandler.parse_request()` 方法：

```python
# ✅ 推荐 - 使用 parse_request
async def post(self, name: str) -> None:
    request = self.parse_request(CreateRoomRequest)
    # 使用 request.name, request.type 等

# ❌ 不推荐 - 手动解析
async def post(self, name: str) -> None:
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
async def get(self, name: str, room_name: str) -> None:
    # name 和 room_name 来自 URL 路径
    # /teams/{name}/rooms/{room_name}.json
```

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
    error_message=f"Team '{name}' not found",
    error_code="team_not_found"
)

# 检查对象非空
assertUtil.assertNotNull(
    room,
    error_message=f"Room '{room_name}' not found",
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
  "error_desc": "Team 'default' not found"
}
```

---

## 错误处理

### 不需要 try-catch

Controller 中**不需要**手动捕获异常：

```python
# ✅ 推荐 - 直接抛出异常
async def post(self, name: str) -> None:
    exists = await gtTeamManager.team_exists(name)
    assertUtil.assertTrue(exists, error_message="Team not found", error_code="team_not_found")
    # 业务逻辑...

# ❌ 不推荐 - 手动捕获
async def post(self, name: str) -> None:
    try:
        exists = await gtTeamManager.team_exists(name)
        if not exists:
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

## URL 定义规范

### 命名规则

| 资源类型 | URL 格式 | 示例 |
|----------|----------|------|
| 列表 | `/{资源}s.{扩展名}` | `/teams.list.json` |
| 详情 | `/{资源}/{id}.{扩展名}` | `/teams/default.json` |
| 创建 | `/{资源}/create.{扩展名}` | `/teams/create.json` |
| 修改 | `/{资源}/{id}/modify.{扩展名}` | `/teams/default/modify.json` |
| 删除 | `/{资源}/{id}/delete.{扩展名}` | `/teams/default/delete.json` |
| 子资源列表 | `/{父资源}/{父id}/{子资源}s.{扩展名}` | `/teams/default/rooms.json` |
| 子资源详情 | `/{父资源}/{父id}/{子资源}/{子id}.{扩展名}` | `/teams/default/rooms/test.json` |

### HTTP 方法约定

| 操作 | HTTP 方法 | 说明 |
|------|-----------|------|
| 查询 | `GET` | 获取数据 |
| 创建/修改/删除 | `POST` | 统一使用 POST（简化调用） |

---

## 路由注册

### 在 `route.py` 中注册路由

```python
import tornado.web
from controller import teamController, roomController

application = tornado.web.Application([
    # Team
    (r"/teams/list.json",                   teamController.TeamListHandler),
    (r"/teams/create.json",                 teamController.TeamCreateHandler),
    (r"/teams/([^/]+).json",                teamController.TeamDetailHandler),
    (r"/teams/([^/]+)/modify.json",         teamController.TeamModifyHandler),
    (r"/teams/([^/]+)/delete.json",         teamController.TeamDeleteHandler),

    # Team Rooms
    (r"/teams/([^/]+)/rooms.json",          roomController.TeamRoomsHandler),
    (r"/teams/([^/]+)/rooms/([^/]+).json",  roomController.TeamRoomDetailHandler),
    (r"/teams/([^/]+)/rooms/([^/]+)/modify.json", roomController.TeamRoomModifyHandler),
    (r"/teams/([^/]+)/rooms/([^/]+)/delete.json", roomController.TeamRoomDeleteHandler),
], **tornado_settings)
```

### 路由参数

使用 `([^/]+)` 匹配路径参数，参数按顺序传递给 handler 方法：

```python
# URL: /teams/default/rooms/test.json
# 路由: (r"/teams/([^/]+)/rooms/([^/]+).json", Handler)

async def get(self, name: str, room_name: str) -> None:
    # name = "default"
    # room_name = "test"
```

---

## 完整示例

### 示例：Team 房间管理 Controller

```python
# controller/roomController.py
import json
from typing import List
from pydantic import BaseModel
from controller.baseController import BaseHandler
from dal.db import gtTeamManager, gtRoomManager
from service import teamService
from constants import RoomType
from util import assertUtil

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
    members: List[str]

# Handler: 获取 Team 下的所有 Room
class TeamRoomsHandler(BaseHandler):
    async def get(self, name: str) -> None:
        # 验证 Team 存在
        exists = await gtTeamManager.team_exists(name)
        assertUtil.assertTrue(exists, error_message=f"Team '{name}' not found", error_code="team_not_found")

        # 获取房间列表
        rooms = await gtRoomManager.get_rooms_by_team(name)
        self.return_json({"rooms": rooms})

# Handler: 创建 Room
class TeamRoomCreateHandler(BaseHandler):
    async def post(self, name: str) -> None:
        # 解析请求
        request = self.parse_request(CreateRoomRequest)

        # 验证
        exists = await gtTeamManager.team_exists(name)
        assertUtil.assertTrue(exists, error_message=f"Team '{name}' not found", error_code="team_not_found")

        existing_rooms = await gtRoomManager.get_rooms_by_team(name)
        existing = next((r for r in existing_rooms if r.name == request.name), None)
        assertUtil.assertEqual(existing, None, error_message=f"Room already exists", error_code="room_exists")

        # 业务逻辑
        room_config = {
            "name": request.name,
            "type": RoomType(request.type),
            "initial_topic": request.initial_topic,
            "max_turns": request.max_turns,
        }
        await gtRoomManager.upsert_rooms(name, [room_config])
        await teamService.hot_reload_team(name)

        # 返回
        self.return_json({"status": "created", "room_name": request.name})

# Handler: 更新 Room
class TeamRoomModifyHandler(BaseHandler):
    async def post(self, name: str, room_name: str) -> None:
        request = self.parse_request(UpdateRoomRequest)

        exists = await gtTeamManager.team_exists(name)
        assertUtil.assertTrue(exists, error_message=f"Team not found", error_code="team_not_found")

        existing_rooms = await gtRoomManager.get_rooms_by_team(name)
        existing = next((r for r in existing_rooms if r.name == room_name), None)
        assertUtil.assertNotNull(existing, error_message=f"Room not found", error_code="room_not_found")

        # 更新逻辑...
        await gtRoomManager.upsert_rooms(name, all_rooms)
        await teamService.hot_reload_team(name)

        self.return_json({"status": "updated", "room_name": room_name})

# Handler: 删除 Room
class TeamRoomDeleteHandler(BaseHandler):
    async def post(self, name: str, room_name: str) -> None:
        exists = await gtTeamManager.team_exists(name)
        assertUtil.assertTrue(exists, error_message=f"Team not found", error_code="team_not_found")

        existing_rooms = await gtRoomManager.get_rooms_by_team(name)
        existing = next((r for r in existing_rooms if r.name == room_name), None)
        assertUtil.assertNotNull(existing, error_message=f"Room not found", error_code="room_not_found")

        # 删除逻辑...
        await gtRoomManager.upsert_rooms(name, remaining_rooms)
        await gtRoomMemberManager.delete_members_by_room(room_key)
        await teamService.hot_reload_team(name)

        self.return_json({"status": "deleted", "room_name": room_name})
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