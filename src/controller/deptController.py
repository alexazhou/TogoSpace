import json
from pydantic import BaseModel

from controller.baseController import BaseHandler
from dal.db import gtTeamManager
from model.dbModel.gtDept import GtDept
from service import deptService, teamService
from util import assertUtil


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

        dept_tree = self._parse_dept_tree(self._get_request_json())
        await deptService.overwrite_dept_tree(team_id, dept_tree)

        # 触发热更新
        await teamService.hot_reload_team(team.name)

        self.return_success()

    def _parse_dept_tree(self, data: dict) -> GtDept:
        """从 JSON 字典构建 GtDept 树。"""
        children = [self._parse_dept_tree(child) for child in data.get("children", [])]
        return GtDept(
            id=data.get("id"),
            team_id=data.get("team_id"),
            name=data.get("name", ""),
            responsibility=data.get("responsibility", ""),
            parent_id=data.get("parent_id"),
            manager_id=data.get("manager_id"),
            agent_ids=data.get("agent_ids", []),
            children=children,
        )

    def _get_request_json(self) -> dict:
        return json.loads(self.request.body)
