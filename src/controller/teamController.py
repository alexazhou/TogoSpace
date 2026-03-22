import json
from typing import List
from pydantic import BaseModel

from controller.baseController import BaseHandler
from dal.db import gtTeamManager, gtRoomManager, gtRoomMemberManager
from constants import RoomType, enum_to_str


# Request Models
class CreateTeamRequest(BaseModel):
    name: str
    max_function_calls: int | None = None
    rooms: List[dict]


class UpdateTeamRequest(BaseModel):
    name: str
    max_function_calls: int | None = None
    rooms: List[dict] | None = None


class CreateRoomRequest(BaseModel):
    name: str
    type: str
    initial_topic: str | None = None
    max_turns: int = 100


class UpdateRoomRequest(BaseModel):
    type: str | None = None
    initial_topic: str | None = None
    max_turns: int | None = None


class UpdateMembersRequest(BaseModel):
    members: List[str]


class TeamListHandler(BaseHandler):
    """GET /teams - 获取所有 Team 列表"""

    async def get(self):
        teams = await gtTeamManager.get_all_teams()
        data = [
            {
                "name": team.name,
                "max_function_calls": team.max_function_calls,
                "enabled": team.enabled,
                "created_at": team.created_at,
                "updated_at": team.updated_at,
            }
            for team in teams
        ]
        self.return_json({"teams": data})

    """POST /teams - 创建新 Team（自动触发热更新）"""

    async def post(self):
        try:
            body = json.loads(self.request.body)
            request = CreateTeamRequest(**body)
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            self.set_status(400)
            self.return_json({"error": f"invalid request: {e}"})
            return

        try:
            # 检查 Team 是否已存在
            if await gtTeamManager.team_exists(request.name):
                self.set_status(409)
                self.return_json({"error": f"Team '{request.name}' already exists"})
                return

            # 创建 Team
            await gtTeamManager.upsert_team({
                "name": request.name,
                "max_function_calls": request.max_function_calls,
            })

            # 创建 Rooms
            await gtRoomManager.upsert_rooms(request.name, request.rooms)

            # 创建 Members
            for room in request.rooms:
                room_name = room["name"]
                room_key = f"{room_name}@{request.name}"
                members = room.get("members", [])
                await gtRoomMemberManager.upsert_room_members(room_key, members)

            # 触发热更新
            from service.teamConfigService import hot_reload_team
            await hot_reload_team(request.name)

            self.set_status(201)
            self.return_json({"status": "created", "name": request.name})
        except Exception as e:
            self.set_status(500)
            self.return_json({"error": str(e)})


class TeamDetailHandler(BaseHandler):
    """GET /teams/{name} - 获取指定 Team 详情"""

    async def get(self, name: str):
        config = await gtTeamManager.get_team_config(name)
        if config is None:
            self.set_status(404)
            self.return_json({"error": f"Team '{name}' not found"})
            return

        # 将 groups 转换为 rooms 以保持 API 一致性
        config["rooms"] = config.pop("groups")
        self.return_json(config)

    """PUT /teams/{name} - 更新 Team 配置（自动触发热更新）"""

    async def put(self, name: str):
        try:
            body = json.loads(self.request.body)
            request = UpdateTeamRequest(**body)
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            self.set_status(400)
            self.return_json({"error": f"invalid request: {e}"})
            return

        # 确保 name 匹配
        if request.name != name:
            self.set_status(400)
            self.return_json({"error": "name mismatch"})
            return

        try:
            # 检查 Team 是否存在
            if not await gtTeamManager.team_exists(name):
                self.set_status(404)
                self.return_json({"error": f"Team '{name}' not found"})
                return

            # 更新配置
            from service.teamConfigService import update_team
            team_config = {
                "name": name,
                "max_function_calls": request.max_function_calls,
            }

            # 将 rooms 转换为 groups 以兼容内部逻辑
            if request.rooms is not None:
                team_config["groups"] = request.rooms

            await update_team(team_config)

            self.return_json({"status": "updated", "name": name})
        except Exception as e:
            self.set_status(500)
            self.return_json({"error": str(e)})

    """DELETE /teams/{name} - 删除 Team（自动触发热更新）"""

    async def delete(self, name: str):
        try:
            # 检查 Team 是否存在
            if not await gtTeamManager.team_exists(name):
                self.set_status(404)
                self.return_json({"error": f"Team '{name}' not found"})
                return

            # 删除 Team
            from service.teamConfigService import delete_team
            await delete_team(name)

            self.return_json({"status": "deleted", "name": name})
        except Exception as e:
            self.set_status(500)
            self.return_json({"error": str(e)})


class TeamRoomsHandler(BaseHandler):
    """GET /teams/{name}/rooms - 获取 Team 下的所有 Room"""

    async def get(self, name: str):
        # 检查 Team 是否存在
        if not await gtTeamManager.team_exists(name):
            self.set_status(404)
            self.return_json({"error": f"Team '{name}' not found"})
            return

        rooms = await gtRoomManager.get_rooms_by_team(name)
        data = [
            {
                "name": room.name,
                "type": enum_to_str(room.type),
                "initial_topic": room.initial_topic,
                "max_turns": room.max_turns,
            }
            for room in rooms
        ]
        self.return_json({"rooms": data})

    """POST /teams/{name}/rooms/{room_name} - 添加 Room"""

    async def post(self, name: str, room_name: str):
        try:
            body = json.loads(self.request.body)
            request = CreateRoomRequest(**body)
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            self.set_status(400)
            self.return_json({"error": f"invalid request: {e}"})
            return

        # 检查 Team 是否存在
        if not await gtTeamManager.team_exists(name):
            self.set_status(404)
            self.return_json({"error": f"Team '{name}' not found"})
            return

        try:
            # 获取现有 Rooms
            existing_rooms = await gtRoomManager.get_rooms_by_team(name)
            existing_names = {r.name for r in existing_rooms}

            if room_name in existing_names:
                self.set_status(409)
                self.return_json({"error": f"Room '{room_name}' already exists"})
                return

            # 构建新的 Rooms 列表（添加新 Room）
            all_rooms = [
                {
                    "name": r.name,
                    "type": enum_to_str(r.type),
                    "initial_topic": r.initial_topic,
                    "max_turns": r.max_turns,
                }
                for r in existing_rooms
            ]
            all_rooms.append({
                "name": room_name,
                "type": request.type,
                "initial_topic": request.initial_topic or "",
                "max_turns": request.max_turns,
            })

            # 更新 Rooms
            await gtRoomManager.upsert_rooms(name, all_rooms)

            # 触发热更新
            from service.teamConfigService import hot_reload_team
            await hot_reload_team(name)

            self.set_status(201)
            self.return_json({"status": "created", "room_name": room_name})
        except Exception as e:
            self.set_status(500)
            self.return_json({"error": str(e)})


class TeamRoomDetailHandler(BaseHandler):
    """PUT /teams/{name}/rooms/{room_name} - 更新 Room"""

    async def put(self, name: str, room_name: str):
        try:
            body = json.loads(self.request.body)
            request = UpdateRoomRequest(**body)
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            self.set_status(400)
            self.return_json({"error": f"invalid request: {e}"})
            return

        # 检查 Team 是否存在
        if not await gtTeamManager.team_exists(name):
            self.set_status(404)
            self.return_json({"error": f"Team '{name}' not found"})
            return

        try:
            # 获取现有 Rooms
            existing_rooms = await gtRoomManager.get_rooms_by_team(name)
            existing = next((r for r in existing_rooms if r.name == room_name), None)
            if existing is None:
                self.set_status(404)
                self.return_json({"error": f"Room '{room_name}' not found"})
                return

            # 更新 Room
            room_type = RoomType(request.type) if request.type else existing.type
            initial_topic = request.initial_topic if request.initial_topic is not None else existing.initial_topic
            max_turns = request.max_turns if request.max_turns is not None else existing.max_turns

            # 构建新的 Rooms 列表（更新指定 Room）
            all_rooms = []
            for r in existing_rooms:
                if r.name == room_name:
                    all_rooms.append({
                        "name": room_name,
                        "type": room_type,
                        "initial_topic": initial_topic,
                        "max_turns": max_turns,
                    })
                else:
                    all_rooms.append({
                        "name": r.name,
                        "type": enum_to_str(r.type),
                        "initial_topic": r.initial_topic,
                        "max_turns": r.max_turns,
                    })

            await gtRoomManager.upsert_rooms(name, all_rooms)

            # 触发热更新
            from service.teamConfigService import hot_reload_team
            await hot_reload_team(name)

            self.return_json({"status": "updated", "room_name": room_name})
        except Exception as e:
            self.set_status(500)
            self.return_json({"error": str(e)})

    """DELETE /teams/{name}/rooms/{room_name} - 删除 Room"""

    async def delete(self, name: str, room_name: str):
        # 检查 Team 是否存在
        if not await gtTeamManager.team_exists(name):
            self.set_status(404)
            self.return_json({"error": f"Team '{name}' not found"})
            return

        try:
            # 获取现有 Rooms
            existing_rooms = await gtRoomManager.get_rooms_by_team(name)
            existing = next((r for r in existing_rooms if r.name == room_name), None)
            if existing is None:
                self.set_status(404)
                self.return_json({"error": f"Room '{room_name}' not found"})
                return

            # 删除 Room（通过重新插入其他 Rooms）
            room_key = f"{room_name}@{name}"
            remaining_rooms = [r for r in existing_rooms if r.name != room_name]

            await gtRoomManager.upsert_rooms(name, [
                {
                    "name": r.name,
                    "type": enum_to_str(r.type),
                    "initial_topic": r.initial_topic,
                    "max_turns": r.max_turns,
                }
                for r in remaining_rooms
            ])

            # 删除 Members
            await gtRoomMemberManager.delete_members_by_room(room_key)

            # 触发热更新
            from service.teamConfigService import hot_reload_team
            await hot_reload_team(name)

            self.return_json({"status": "deleted", "room_name": room_name})
        except Exception as e:
            self.set_status(500)
            self.return_json({"error": str(e)})


class RoomMembersHandler(BaseHandler):
    """GET /teams/{name}/rooms/{room_name}/members - 获取 Room 成员"""

    async def get(self, name: str, room_name: str):
        # 检查 Team 是否存在
        if not await gtTeamManager.team_exists(name):
            self.set_status(404)
            self.return_json({"error": f"Team '{name}' not found"})
            return

        room_key = f"{room_name}@{name}"
        members = await gtRoomMemberManager.get_members_by_room(room_key)
        self.return_json({"members": members})

    """PUT /teams/{name}/rooms/{room_name}/members - 更新 Room 成员"""

    async def put(self, name: str, room_name: str):
        try:
            body = json.loads(self.request.body)
            request = UpdateMembersRequest(**body)
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            self.set_status(400)
            self.return_json({"error": f"invalid request: {e}"})
            return

        # 检查 Team 是否存在
        if not await gtTeamManager.team_exists(name):
            self.set_status(404)
            self.return_json({"error": f"Team '{name}' not found"})
            return

        try:
            # 获取现有 Room
            existing_rooms = await gtRoomManager.get_rooms_by_team(name)
            existing = next((r for r in existing_rooms if r.name == room_name), None)
            if existing is None:
                self.set_status(404)
                self.return_json({"error": f"Room '{room_name}' not found"})
                return

            # 更新 Members
            room_key = f"{room_name}@{name}"
            await gtRoomMemberManager.upsert_room_members(room_key, request.members)

            # 触发热更新
            from service.teamConfigService import hot_reload_team
            await hot_reload_team(name)

            self.return_json({"status": "updated", "room_name": room_name})
        except Exception as e:
            self.set_status(500)
            self.return_json({"error": str(e)})