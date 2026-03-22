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
    groups: List[dict]


class UpdateTeamRequest(BaseModel):
    name: str
    max_function_calls: int | None = None
    groups: List[dict] | None = None


class CreateGroupRequest(BaseModel):
    name: str
    type: str
    initial_topic: str | None = None
    max_turns: int = 100


class UpdateGroupRequest(BaseModel):
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
            await gtRoomManager.upsert_rooms(request.name, request.groups)

            # 创建 Members
            for group in request.groups:
                room_name = group["name"]
                room_key = f"{room_name}@{request.name}"
                members = group.get("members", [])
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

            if request.groups is not None:
                team_config["groups"] = request.groups

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


class TeamGroupsHandler(BaseHandler):
    """GET /teams/{name}/groups - 获取 Team 下的所有 Group"""

    async def get(self, name: str):
        # 检查 Team 是否存在
        if not await gtTeamManager.team_exists(name):
            self.set_status(404)
            self.return_json({"error": f"Team '{name}' not found"})
            return

        groups = await gtRoomManager.get_rooms_by_team(name)
        data = [
            {
                "name": group.name,
                "type": enum_to_str(group.type),
                "initial_topic": group.initial_topic,
                "max_turns": group.max_turns,
            }
            for group in groups
        ]
        self.return_json({"groups": data})

    """POST /teams/{name}/groups/{group_name} - 添加 Group"""

    async def post(self, name: str, group_name: str):
        try:
            body = json.loads(self.request.body)
            request = CreateGroupRequest(**body)
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
            # 获取现有 Groups
            existing_groups = await gtRoomManager.get_rooms_by_team(name)
            existing_names = {g.name for g in existing_groups}

            if group_name in existing_names:
                self.set_status(409)
                self.return_json({"error": f"Group '{group_name}' already exists"})
                return

            # 构建新的 Groups 列表（添加新 Group）
            all_groups = [
                {
                    "name": g.name,
                    "type": enum_to_str(g.type),
                    "initial_topic": g.initial_topic,
                    "max_turns": g.max_turns,
                }
                for g in existing_groups
            ]
            all_groups.append({
                "name": group_name,
                "type": request.type,
                "initial_topic": request.initial_topic or "",
                "max_turns": request.max_turns,
            })

            # 更新 Rooms
            await gtRoomManager.upsert_rooms(name, all_groups)

            # 触发热更新
            from service.teamConfigService import hot_reload_team
            await hot_reload_team(name)

            self.set_status(201)
            self.return_json({"status": "created", "group_name": group_name})
        except Exception as e:
            self.set_status(500)
            self.return_json({"error": str(e)})


class TeamGroupDetailHandler(BaseHandler):
    """PUT /teams/{name}/groups/{group_name} - 更新 Group"""

    async def put(self, name: str, group_name: str):
        try:
            body = json.loads(self.request.body)
            request = UpdateGroupRequest(**body)
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
            # 获取现有 Groups
            existing_groups = await gtRoomManager.get_rooms_by_team(name)
            existing = next((g for g in existing_groups if g.name == group_name), None)
            if existing is None:
                self.set_status(404)
                self.return_json({"error": f"Group '{group_name}' not found"})
                return

            # 更新 Group
            group_type = RoomType(request.type) if request.type else existing.type
            initial_topic = request.initial_topic if request.initial_topic is not None else existing.initial_topic
            max_turns = request.max_turns if request.max_turns is not None else existing.max_turns

            # 构建新的 Groups 列表（更新指定 Group）
            all_groups = []
            for g in existing_groups:
                if g.name == group_name:
                    all_groups.append({
                        "name": group_name,
                        "type": group_type,
                        "initial_topic": initial_topic,
                        "max_turns": max_turns,
                    })
                else:
                    all_groups.append({
                        "name": g.name,
                        "type": enum_to_str(g.type),
                        "initial_topic": g.initial_topic,
                        "max_turns": g.max_turns,
                    })

            await gtRoomManager.upsert_rooms(name, all_groups)

            # 触发热更新
            from service.teamConfigService import hot_reload_team
            await hot_reload_team(name)

            self.return_json({"status": "updated", "group_name": group_name})
        except Exception as e:
            self.set_status(500)
            self.return_json({"error": str(e)})

    """DELETE /teams/{name}/groups/{group_name} - 删除 Group"""

    async def delete(self, name: str, group_name: str):
        # 检查 Team 是否存在
        if not await gtTeamManager.team_exists(name):
            self.set_status(404)
            self.return_json({"error": f"Team '{name}' not found"})
            return

        try:
            # 获取现有 Groups
            existing_groups = await gtRoomManager.get_rooms_by_team(name)
            existing = next((g for g in existing_groups if g.name == group_name), None)
            if existing is None:
                self.set_status(404)
                self.return_json({"error": f"Group '{group_name}' not found"})
                return

            # 删除 Group（通过重新插入其他 Groups）
            room_key = f"{group_name}@{name}"
            remaining_groups = [g for g in existing_groups if g.name != group_name]

            await gtRoomManager.upsert_rooms(name, [
                {
                    "name": g.name,
                    "type": enum_to_str(g.type),
                    "initial_topic": g.initial_topic,
                    "max_turns": g.max_turns,
                }
                for g in remaining_groups
            ])

            # 删除 Members
            await gtRoomMemberManager.delete_members_by_room(room_key)

            # 触发热更新
            from service.teamConfigService import hot_reload_team
            await hot_reload_team(name)

            self.return_json({"status": "deleted", "group_name": group_name})
        except Exception as e:
            self.set_status(500)
            self.return_json({"error": str(e)})


class GroupMembersHandler(BaseHandler):
    """GET /teams/{name}/groups/{group_name}/members - 获取 Group 成员"""

    async def get(self, name: str, group_name: str):
        # 检查 Team 是否存在
        if not await gtTeamManager.team_exists(name):
            self.set_status(404)
            self.return_json({"error": f"Team '{name}' not found"})
            return

        room_key = f"{group_name}@{name}"
        members = await gtRoomMemberManager.get_members_by_room(room_key)
        self.return_json({"members": members})

    """PUT /teams/{name}/groups/{group_name}/members - 更新 Group 成员"""

    async def put(self, name: str, group_name: str):
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
            # 获取现有 Group
            existing_groups = await gtRoomManager.get_rooms_by_team(name)
            existing = next((g for g in existing_groups if g.name == group_name), None)
            if existing is None:
                self.set_status(404)
                self.return_json({"error": f"Group '{group_name}' not found"})
                return

            # 更新 Members
            room_key = f"{group_name}@{name}"
            await gtRoomMemberManager.upsert_room_members(room_key, request.members)

            # 触发热更新
            from service.teamConfigService import hot_reload_team
            await hot_reload_team(name)

            self.return_json({"status": "updated", "group_name": group_name})
        except Exception as e:
            self.set_status(500)
            self.return_json({"error": str(e)})