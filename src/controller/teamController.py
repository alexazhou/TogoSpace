# 标准库
import json

# 第三方包
from pydantic import BaseModel

# 内部包
from controller.baseController import BaseHandler
from dal.db import gtTeamManager
from constants import enum_to_str
from service import teamService
from model.coreModel.gtCoreWebModel import TeamInfo


# Request Models
class CreateTeamRequest(BaseModel):
    name: str
    max_function_calls: int | None = None
    rooms: list[dict]


class UpdateTeamRequest(BaseModel):
    max_function_calls: int | None = None
    rooms: list[dict] | None = None


class TeamListHandler(BaseHandler):
    """GET /teams/list.json - 获取所有 Team 列表"""

    async def get(self):
        teams = await gtTeamManager.get_all_teams()
        data = [
            TeamInfo(
                name=team.name,
                max_function_calls=team.max_function_calls,
                enabled=team.enabled,
                created_at=team.created_at,
                updated_at=team.updated_at,
            ).model_dump(mode="json")
            for team in teams
        ]
        self.return_json({"teams": data})


class TeamCreateHandler(BaseHandler):
    """POST /teams/create.json - 创建新 Team（自动触发热更新）"""

    async def post(self):
        try:
            body = json.loads(self.request.body)
            request = CreateTeamRequest(**body)
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            self.set_status(400)
            self.return_json({"error": f"invalid request: {e}"})
            return

        try:
            # 转换 rooms 为 groups 格式
            team_config = {
                "name": request.name,
                "max_function_calls": request.max_function_calls,
                "groups": request.rooms,
            }

            # 调用 service 创建 team
            await teamService.create_team(team_config)

            self.return_json({"status": "created", "name": request.name})
        except ValueError as e:
            self.set_status(409)
            self.return_json({"error": str(e)})
        except Exception as e:
            self.set_status(500)
            self.return_json({"error": str(e)})


class TeamDetailHandler(BaseHandler):
    """GET /teams/{name}.json - 获取指定 Team 详情"""

    async def get(self, name: str):
        config = await gtTeamManager.get_team_config(name)
        if config is None:
            self.set_status(404)
            self.return_json({"error": f"Team '{name}' not found"})
            return

        # 将 groups 转换为 rooms 以保持 API 一致性
        config["rooms"] = config.pop("groups")
        self.return_json(config)


class TeamModifyHandler(BaseHandler):
    """PUT /teams/{name}/modify.json - 更新 Team 配置（自动触发热更新）"""

    async def put(self, name: str):
        try:
            body = json.loads(self.request.body)
            request = UpdateTeamRequest(**body)
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            self.set_status(400)
            self.return_json({"error": f"invalid request: {e}"})
            return

        try:
            # 检查 Team 是否存在
            if not await gtTeamManager.team_exists(name):
                self.set_status(404)
                self.return_json({"error": f"Team '{name}' not found"})
                return

            # 构建配置
            team_config = {
                "name": name,
                "max_function_calls": request.max_function_calls,
            }

            # 将 rooms 转换为 groups 以兼容内部逻辑
            if request.rooms is not None:
                team_config["groups"] = request.rooms

            # 调用 service 更新 team
            await teamService.update_team(team_config)

            self.return_json({"status": "updated", "name": name})
        except Exception as e:
            self.set_status(500)
            self.return_json({"error": str(e)})


class TeamDeleteHandler(BaseHandler):
    """DELETE /teams/{name}/delete.json - 删除 Team（自动触发热更新）"""

    async def delete(self, name: str):
        try:
            # 检查 Team 是否存在
            if not await gtTeamManager.team_exists(name):
                self.set_status(404)
                self.return_json({"error": f"Team '{name}' not found"})
                return

            # 调用 service 删除 team
            await teamService.delete_team(name)

            self.return_json({"status": "deleted", "name": name})
        except Exception as e:
            self.set_status(500)
            self.return_json({"error": str(e)})