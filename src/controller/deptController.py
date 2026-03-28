from pydantic import BaseModel

from controller.baseController import BaseHandler
from dal.db import gtTeamManager
from service import deptService, teamService
from util import assertUtil
from util.configTypes import DeptNodeConfig


class SetDeptTreeRequest(BaseModel):
    dept_tree: DeptNodeConfig


class DeptTreeHandler(BaseHandler):
    """GET/PUT /teams/<id>/dept_tree.json - 获取/设置部门树"""

    async def get(self, team_id_str: str) -> None:
        team_id = int(team_id_str)
        team = await gtTeamManager.get_team_by_id(team_id)
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")

        tree = await deptService.get_dept_tree_async(team_id)
        self.return_json({"dept_tree": tree.model_dump() if tree else None})

    async def put(self, team_id_str: str) -> None:
        team_id = int(team_id_str)
        team = await gtTeamManager.get_team_by_id(team_id)
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")

        request = self.parse_request(SetDeptTreeRequest)
        await deptService.set_dept_tree(team_id, request.dept_tree)

        # 触发热更新
        await teamService.hot_reload_team(team.name)

        self.return_json({"status": "ok"})