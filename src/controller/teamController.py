# 标准库
from pydantic import BaseModel

# 内部包
from controller.baseController import BaseHandler
from dal.db import gtTeamManager
from service import teamService
from util import assertUtil


# Request Models
class CreateTeamRequest(BaseModel):
    name: str
    members: list[str]
    preset_rooms: list[dict]


class UpdateTeamRequest(BaseModel):
    members: list[str] | None = None
    preset_rooms: list[dict] | None = None


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
            "members": request.members,
            "preset_rooms": request.preset_rooms,
        }

        # 调用 service 创建 team
        await teamService.create_team(team_config)

        self.return_json({"status": "created", "name": request.name})


class TeamDetailHandler(BaseHandler):
    """GET /teams/{id}.json - 获取指定 Team 详情"""

    async def get(self, team_id_str: str) -> None:
        team_id = int(team_id_str)
        team = await gtTeamManager.get_team_by_id(team_id)
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")

        self.return_json(team)


class TeamModifyHandler(BaseHandler):
    """POST /teams/{id}/modify.json - 更新 Team 配置（自动触发热更新）"""

    async def post(self, team_id_str: str) -> None:
        request = self.parse_request(UpdateTeamRequest)

        # 通过 ID 获取 Team
        team_id = int(team_id_str)
        team = await gtTeamManager.get_team_by_id(team_id)
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")

        team_name = team.name

        # 构建配置
        team_config = {
            "name": team_name,
        }

        if request.members is not None:
            team_config["members"] = request.members
        if request.preset_rooms is not None:
            team_config["preset_rooms"] = request.preset_rooms

        # 调用 service 更新 team
        await teamService.update_team(team_config)

        self.return_json({"status": "updated", "name": team_name})


class TeamDeleteHandler(BaseHandler):
    """POST /teams/{id}/delete.json - 删除 Team（自动触发热更新）"""

    async def post(self, team_id_str: str) -> None:
        # 通过 ID 获取 Team
        team_id = int(team_id_str)
        team = await gtTeamManager.get_team_by_id(team_id)
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")

        team_name = team.name

        # 调用 service 删除 team
        await teamService.delete_team(team_name)

        self.return_json({"status": "deleted", "name": team_name})
