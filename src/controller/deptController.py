import json

from pydantic import BaseModel

from controller.baseController import BaseHandler
from dal.db import gtTeamManager, gtAgentManager
from service import deptService
from util import assertUtil
from constants import EmployStatus


class MoveMemberRequest(BaseModel):
    member: str
    is_manager: bool = False


class RemoveMemberRequest(BaseModel):
    new_manager: str | None = None


class SetManagerRequest(BaseModel):
    manager: str


class DeptTreeHandler(BaseHandler):
    """GET /teams/<id>/dept_tree.json - 获取部门树"""

    async def get(self, team_id_str: str) -> None:
        team_id = int(team_id_str)
        team = await gtTeamManager.get_team_by_id(team_id)
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")

        tree = await deptService.get_dept_tree_async(team_id)
        self.return_json({"dept_tree": tree.model_dump() if tree else None})


class DeptManagerHandler(BaseHandler):
    """PUT /teams/<id>/dept_tree/<dept>/manager.json - 变更部门主管"""

    async def put(self, team_id_str: str, dept_name: str) -> None:
        team_id = int(team_id_str)
        team = await gtTeamManager.get_team_by_id(team_id)
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")

        request = self.parse_request(SetManagerRequest)
        await deptService.set_dept_manager(team_id, dept_name, request.manager)
        self.return_json({"status": "ok"})


class DeptMembersHandler(BaseHandler):
    """POST /teams/<id>/dept_tree/<dept>/agents.json - 将成员加入部门"""

    async def post(self, team_id_str: str, dept_name: str) -> None:
        team_id = int(team_id_str)
        team = await gtTeamManager.get_team_by_id(team_id)
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")

        request = self.parse_request(MoveMemberRequest)
        await deptService.move_member(team_id, request.member, dept_name, is_manager=request.is_manager)
        self.return_json({"status": "ok"})


class DeptMemberDetailHandler(BaseHandler):
    """DELETE /teams/<id>/dept_tree/<dept>/agents/<agent>.json - 将成员移出部门"""

    async def delete(self, team_id_str: str, dept_name: str, agent_name: str) -> None:
        team_id = int(team_id_str)
        team = await gtTeamManager.get_team_by_id(team_id)
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")

        body = self.request.body
        new_manager: str | None = None
        if body:
            try:
                data = json.loads(body)
                new_manager = data.get("new_manager")
            except Exception:
                pass

        await deptService.remove_member(team_id, agent_name, new_manager=new_manager)
        self.return_json({"status": "ok"})


class DeptOffBoardMembersHandler(BaseHandler):
    """GET /teams/<id>/dept_agents.json?employ_status=off_board - 查询休闲成员"""

    async def get(self, team_id_str: str) -> None:
        team_id = int(team_id_str)
        team = await gtTeamManager.get_team_by_id(team_id)
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")

        employ_status_raw = self.get_query_argument("employ_status", "OFF_BOARD")
        if EmployStatus.value_of(employ_status_raw) == EmployStatus.OFF_BOARD:
            agents = await deptService.get_off_board_members(team_id)
        else:
            agents = await gtAgentManager.get_agents_by_team(team_id)
        data = [
            {"id": m.id, "name": m.name, "role_template": m.role_template_name, "employ_status": m.employ_status.name if m.employ_status else None}
            for m in agents
        ]

        self.return_json({"agents": data})
