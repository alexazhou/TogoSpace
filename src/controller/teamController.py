import json
from pydantic import BaseModel

from controller.baseController import BaseHandler
from dal.db import gtTeamManager, gtRoomManager, gtRoomMemberManager
from constants import enum_to_str


# Request Models
class CreateTeamRequest(BaseModel):
    name: str
    max_function_calls: int | None = None
    rooms: list[dict]


class UpdateTeamRequest(BaseModel):
    name: str
    max_function_calls: int | None = None
    rooms: list[dict] | None = None


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