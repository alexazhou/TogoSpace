# 标准库
import json

# 第三方包
from pydantic import BaseModel

# 内部包
from controller.baseController import BaseHandler
from dal.db import gtTeamManager
from service import teamService
from util import assertUtil


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

    async def get(self) -> None:
        teams = await gtTeamManager.get_all_teams()
        self.return_json({"teams": teams})


class TeamCreateHandler(BaseHandler):
    """POST /teams/create.json - 创建新 Team（自动触发热更新）"""

    async def post(self) -> None:
        request = self.parse_request(CreateTeamRequest)

        # 转换 rooms 为 groups 格式
        team_config = {
            "name": request.name,
            "max_function_calls": request.max_function_calls,
            "groups": request.rooms,
        }

        # 调用 service 创建 team
        await teamService.create_team(team_config)

        self.return_json({"status": "created", "name": request.name})


class TeamDetailHandler(BaseHandler):
    """GET /teams/{name}.json - 获取指定 Team 详情"""

    async def get(self, name: str) -> None:
        config = await gtTeamManager.get_team_config(name)
        assertUtil.assertNotNull(config, error_message=f"Team '{name}' not found", error_code="team_not_found")

        # 将 groups 转换为 rooms 以保持 API 一致性
        config["rooms"] = config.pop("groups")
        self.return_json(config)


class TeamModifyHandler(BaseHandler):
    """POST /teams/{name}/modify.json - 更新 Team 配置（自动触发热更新）"""

    async def post(self, name: str) -> None:
        request = self.parse_request(UpdateTeamRequest)

        # 检查 Team 是否存在
        exists = await gtTeamManager.team_exists(name)
        assertUtil.assertTrue(exists, name="team_exists", error_message=f"Team '{name}' not found", error_code="team_not_found")

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


class TeamDeleteHandler(BaseHandler):
    """POST /teams/{name}/delete.json - 删除 Team（自动触发热更新）"""

    async def post(self, name: str) -> None:
        # 检查 Team 是否存在
        exists = await gtTeamManager.team_exists(name)
        assertUtil.assertTrue(exists, name="team_exists", error_message=f"Team '{name}' not found", error_code="team_not_found")

        # 调用 service 删除 team
        await teamService.delete_team(name)

        self.return_json({"status": "deleted", "name": name})