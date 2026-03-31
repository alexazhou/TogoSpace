import json

from controller.baseController import BaseHandler
from dal.db import gtTeamManager
from model.dbModel.gtDept import GtDept
from service import deptService, teamService
from util import assertUtil, jsonUtil


class DeptTreeDetailHandler(BaseHandler):
    """GET /teams/<id>/dept_tree.json - 获取部门树"""

    async def get(self, team_id_str: str) -> None:
        team_id = int(team_id_str)
        team = await gtTeamManager.get_team_by_id(team_id)
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")

        tree = await deptService.get_dept_tree(team_id)
        self.return_json({"dept_tree": tree})


class DeptTreeUpdateHandler(BaseHandler):
    """PUT /teams/<id>/dept_tree/update.json - 更新部门树"""

    async def put(self, team_id_str: str) -> None:
        team_id = int(team_id_str)
        team = await gtTeamManager.get_team_by_id(team_id)
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")

        request_body = json.loads(self.request.body)
        dept_tree = jsonUtil.json_data_to_object(request_body.get("dept_tree", request_body), GtDept)
        await deptService.overwrite_dept_tree(team_id, dept_tree)

        # 触发热更新
        await teamService.hot_reload_team(team.name)

        self.return_success()
