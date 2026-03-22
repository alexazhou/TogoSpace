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

        team_config = {
            "name": request.name,
            "max_function_calls": request.max_function_calls,
            "rooms": request.rooms,
        }

        # 调用 service 创建 team
        await teamService.create_team(team_config)

        self.return_json({"status": "created", "name": request.name})


class TeamDetailHandler(BaseHandler):
    """GET /teams/{id}.json - 获取指定 Team 详情"""

    async def get(self, db_id: str) -> None:
        team = await gtTeamManager.get_team_by_id(int(db_id))
        assertUtil.assertNotNull(team, error_message=f"Team ID '{db_id}' not found", error_code="team_not_found")

        self.return_json(team)


class TeamModifyHandler(BaseHandler):
    """POST /teams/{id}/modify.json - 更新 Team 配置（自动触发热更新）"""

    async def post(self, db_id: str) -> None:
        request = self.parse_request(UpdateTeamRequest)

        # 通过 ID 获取 Team
        team = await gtTeamManager.get_team_by_id(int(db_id))
        assertUtil.assertNotNull(team, error_message=f"Team ID '{db_id}' not found", error_code="team_not_found")

        team_name = team.name

        # 构建配置
        team_config = {
            "name": team_name,
            "max_function_calls": request.max_function_calls,
        }

        if request.rooms is not None:
            team_config["rooms"] = request.rooms

        # 调用 service 更新 team
        await teamService.update_team(team_config)

        self.return_json({"status": "updated", "name": team_name})


class TeamDeleteHandler(BaseHandler):
    """POST /teams/{id}/delete.json - 删除 Team（自动触发热更新）"""

    async def post(self, db_id: str) -> None:
        # 通过 ID 获取 Team
        team = await gtTeamManager.get_team_by_id(int(db_id))
        assertUtil.assertNotNull(team, error_message=f"Team ID '{db_id}' not found", error_code="team_not_found")

        team_name = team.name

        # 调用 service 删除 team
        await teamService.delete_team(team_name)

        self.return_json({"status": "deleted", "name": team_name})