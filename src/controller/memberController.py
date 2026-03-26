from typing import List

import service.agentService as agentService
from service.agentService import TeamMember
from model.coreModel.gtCoreWebModel import GtCoreMemberInfo
from controller.baseController import BaseHandler
from constants import MemberStatus
from dal.db import gtTeamManager
from util import assertUtil, configUtil


class MemberListHandler(BaseHandler):
    async def get(self):
        team_name = self.get_query_argument("team_name", None)
        if team_name:
            members: List[TeamMember] = agentService.get_all_team_members()
            members = [m for m in members if m.team_name == team_name]
            data = [m.get_info().model_dump(mode="json") for m in members]
        else:
            data = [
                GtCoreMemberInfo(
                    name=definition.name,
                    template_name=definition.name,
                    model=definition.model or "",
                    team_name="",
                    status=MemberStatus.IDLE,
                ).model_dump(mode="json")
                for definition in agentService.get_all_agent_definitions()
            ]
        self.return_json({"agents": data})


class MemberDetailHandler(BaseHandler):
    async def get(self, team_id_str: str, member_name: str) -> None:
        team_id = int(team_id_str)
        team = await gtTeamManager.get_team_by_id(team_id)
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")
        if team is None:
            return

        member = agentService.find_team_member(team.name, member_name)
        assertUtil.assertNotNull(
            member,
            error_message=f"Member '{member_name}' not found in team '{team.name}'",
            error_code="member_not_found",
        )
        if member is None:
            return

        definition = agentService.get_agent_definition(member.template_name)
        assertUtil.assertNotNull(
            definition,
            error_message=f"Agent template '{member.template_name}' not found",
            error_code="agent_definition_not_found",
        )
        if definition is None:
            return

        if definition.system_prompt:
            prompt = definition.system_prompt
        else:
            prompt = configUtil.load_prompt(definition.prompt_file)

        self.return_json(
            {
                **member.get_info().model_dump(mode="json"),
                "agent_name": member.template_name,
                "driver_type": member.driver.driver_type,
                "prompt": prompt,
            }
        )
