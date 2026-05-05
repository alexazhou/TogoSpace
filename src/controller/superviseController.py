# 第三方包
from pydantic import BaseModel

# 内部包
from controller.baseController import BaseHandler
from dal.db import gtAgentManager, gtTeamManager
from service import roomService, agentService
from constants import RoomState
from util import assertUtil


class SuperviseRequest(BaseModel):
    content: str
    insert_immediately: bool = True


class AgentSuperviseHandler(BaseHandler):
    """POST /agents/{agent_id}/supervise.json

    向指定 Agent 的私聊控制房间发送追加指令，首次使用时自动建房。
    """

    async def post(self, agent_id_str: str) -> None:
        agent_id = int(agent_id_str)
        request = self.parse_request(SuperviseRequest)

        gt_agent = await gtAgentManager.get_agent_by_id(agent_id)
        assertUtil.assertNotNull(gt_agent, error_message=f"agent_id '{agent_id}' not found", error_code="agent_not_found")

        gt_team = await gtTeamManager.get_team_by_id(gt_agent.team_id)
        assertUtil.assertTrue(
            gt_team is not None and gt_team.enabled,
            error_message="team is not active",
            error_code="team_not_active",
        )

        room, created = await roomService.get_or_create_control_room(gt_agent.team_id, agent_id)
        assertUtil.assertTrue(
            room.state != RoomState.INIT,
            error_message="control room is not ready",
            error_code="control_room_not_ready",
        )

        if request.insert_immediately:
            ai_agents = [a for a in agentService.get_room_agents(room.room_id) if a.gt_agent.id == agent_id]
            assertUtil.assertTrue(
                len(ai_agents) > 0 and ai_agents[0].host_managed_turn_loop,
                error_message="insert_immediately is not supported for this agent's driver",
                error_code="immediate_insert_driver_not_supported",
            )

        assertUtil.assertTrue(
            bool(request.content and request.content.strip()),
            error_message="content is required",
            error_code="invalid_request",
        )
        await room.add_message(room.OPERATOR_MEMBER_ID, request.content, insert_immediately=request.insert_immediately)
        # 仅当 OPERATOR 是当前应发言人时才推进轮次；
        # 若 AI Agent 已是当前发言人（turn_pos 指向 AI），add_message 内部已完成调度唤醒，无需再推进
        if room.get_current_turn_agent_id() == room.OPERATOR_MEMBER_ID:
            await room.finish_turn(room.OPERATOR_MEMBER_ID)

        self.return_json({"room_id": room.room_id, "created": created})
