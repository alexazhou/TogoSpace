# 标准库
from collections import Counter
from typing import List

# 第三方包
from pydantic import BaseModel, Field

# 内部包
from controller.baseController import BaseHandler
from dal.db import gtTeamManager, gtRoomManager, gtAgentManager
from model.dbModel.gtRoom import GtRoom
from service import roomService, teamService, agentService
from service.roomService import ChatRoom
from constants import SpecialAgent, RoomState, RoomType
from util import assertUtil


# Room Config Request Models
class CreateRoomRequest(BaseModel):
    name: str
    type: RoomType = RoomType.GROUP
    initial_topic: str | None = None
    max_turns: int = 100
    agent_ids: List[int] = Field(default_factory=list)


class UpdateRoomRequest(BaseModel):
    type: str
    initial_topic: str | None = None
    max_turns: int | None = None


class UpdateAgentsRequest(BaseModel):
    agent_ids: List[int] = Field(default_factory=list)


class SendMessageRequest(BaseModel):
    content: str | None = None
    insert_immediately: bool = False


class RoomApiResponse(BaseModel):
    model_config = {"extra": "ignore"}

    gt_room: dict
    state: str
    need_scheduling: bool
    current_turn_agent_id: int | None = None
    agents: List[int] = Field(default_factory=list)

    @classmethod
    def from_gt_room(cls, gt_room: GtRoom, runtime_room: ChatRoom | None = None) -> "RoomApiResponse":
        """构建 Room API 响应。
        若传入 runtime_room，则优先使用其运行时状态；
        否则以 IDLE 状态作为默认值（如 team 已禁用）。
        """
        if runtime_room is not None:
            return cls.model_validate(runtime_room.to_dict())
        return cls(
            gt_room=gt_room.to_json(),
            state=RoomState.IDLE.name,
            need_scheduling=False,
            current_turn_agent_id=None,
            agents=list(gt_room.agent_ids or []),
        )


def _infer_room_type_from_agent_ids(agent_ids: List[int]) -> RoomType:
    ai_count = len([
        agent_id for agent_id in agent_ids
        if SpecialAgent.value_of(agent_id) != SpecialAgent.OPERATOR
    ])
    if any(SpecialAgent.value_of(agent_id) == SpecialAgent.OPERATOR for agent_id in agent_ids) and ai_count == 1:
        return RoomType.PRIVATE
    return RoomType.GROUP


async def _assert_agent_ids_in_team(team_id: int, agent_ids: List[int]) -> None:
    if len(agent_ids) == 0:
        return

    system_ids = [
        agent_id for agent_id in agent_ids
        if SpecialAgent.value_of(agent_id) == SpecialAgent.SYSTEM
    ]
    assertUtil.assertEqual(
        len(system_ids),
        0,
        error_message=f"system agent is not allowed in room agents: {system_ids}",
        error_code="system_agent_not_allowed",
    )

    duplicate_ids = sorted([agent_id for agent_id, count in Counter(agent_ids).items() if count > 1])
    assertUtil.assertEqual(
        len(duplicate_ids),
        0,
        error_message=f"agent_ids duplicated: {duplicate_ids}",
        error_code="duplicate_agent_ids",
    )

    normal_agent_ids = [agent_id for agent_id in agent_ids if SpecialAgent.value_of(agent_id) is None]
    gt_agents = await gtAgentManager.get_agents_by_ids(normal_agent_ids)
    id_to_agent = {agent.id: agent for agent in gt_agents}

    missing_ids = [
        agent_id for agent_id in normal_agent_ids
        if agent_id not in id_to_agent
    ]
    assertUtil.assertEqual(
        len(missing_ids),
        0,
        error_message=f"agents not found: {missing_ids}",
        error_code="agent_not_found",
    )

    out_of_team_ids = [agent_id for agent_id in normal_agent_ids if id_to_agent[agent_id].team_id != team_id]
    assertUtil.assertEqual(
        len(out_of_team_ids),
        0,
        error_message=f"agents not in team '{team_id}': {out_of_team_ids}",
        error_code="agent_not_in_team",
    )


async def _get_team_room_or_404(team_id: int, room_id: int) -> GtRoom:
    room = await GtRoom.aio_get_or_none(
        (GtRoom.id == room_id) & (GtRoom.team_id == team_id)
    )
    assertUtil.assertNotNull(room, error_message=f"Room ID '{room_id}' not found", error_code="room_not_found")
    return room


class RoomListHandler(BaseHandler):
    async def get(self) -> None:
        team_id_raw = self.get_query_argument("team_id", None)

        if team_id_raw:
            team_id = int(team_id_raw)
            assertUtil.assertNotNull(
                await gtTeamManager.get_team_by_id(team_id),
                error_message=f"Team ID '{team_id_raw}' not found",
                error_code="team_not_found",
            )
            gt_rooms = await gtRoomManager.get_rooms_by_team(team_id)
            data = [
                RoomApiResponse.from_gt_room(gt_room, roomService.get_room(gt_room.id)).model_dump()
                for gt_room in gt_rooms
            ]
        else:
            all_teams = await gtTeamManager.get_all_teams()
            data = []
            for team in all_teams:
                gt_rooms = await gtRoomManager.get_rooms_by_team(team.id)
                data.extend(
                    RoomApiResponse.from_gt_room(gt_room, roomService.get_room(gt_room.id)).model_dump()
                    for gt_room in gt_rooms
                )

        self.return_json({"rooms": data})


class RoomMessagesHandler(BaseHandler):
    """GET /rooms/{id}/messages/list.json; POST /rooms/{id}/messages/send.json"""

    async def get(self, room_id_str: str) -> None:
        room_id = int(room_id_str)
        gt_room = await GtRoom.aio_get_or_none(GtRoom.id == room_id)
        assertUtil.assertNotNull(gt_room, error_message=f"room_id '{room_id}' not found", error_code="room_not_found")
        gt_team = await gtTeamManager.get_team_by_id(gt_room.team_id)
        team_name = gt_team.name if gt_team else ""

        gt_messages = await roomService.get_room_messages_from_db(room_id)
        self.return_json({
            "room_id": gt_room.id,
            "room_name": gt_room.name,
            "team_name": team_name,
            "messages": gt_messages,
        })

    async def post(self, room_id_str: str) -> None:
        # 通过数据库 ID 获取内存中的 ChatRoom
        request = self.parse_request(SendMessageRequest)
        room_id = int(room_id_str)
        gt_room = await GtRoom.aio_get_or_none(GtRoom.id == room_id)
        assertUtil.assertNotNull(gt_room, error_message=f"room_id '{room_id}' not found", error_code="room_not_found")
        gt_team = await gtTeamManager.get_team_by_id(gt_room.team_id)
        assertUtil.assertTrue(gt_team is not None and gt_team.enabled, error_message="team is not active", error_code="team_not_active")
        room = roomService.get_room(room_id)
        assertUtil.assertNotNull(room, error_message=f"room_id '{room_id}' not found", error_code="room_not_found")
        assertUtil.assertTrue(
            room.state != RoomState.INIT,
            error_message="room is in init state, not activated by runtime services",
            error_code="room_not_ready",
        )
        content = request.content
        assertUtil.assertNotNull(content, error_message="content is required", error_code="invalid_request")

        if request.insert_immediately:
            assertUtil.assertTrue(
                room.room_type == RoomType.PRIVATE,
                error_message="insert_immediately is only supported in PRIVATE rooms",
                error_code="room_immediate_insert_not_supported",
            )
            ai_agents = [
                a for a in agentService.get_room_agents(room_id)
                if a.gt_agent.id != room.OPERATOR_MEMBER_ID
            ]
            assertUtil.assertTrue(
                len(ai_agents) > 0 and ai_agents[0].host_managed_turn_loop,
                error_message="insert_immediately is not supported for this agent's driver",
                error_code="immediate_insert_driver_not_supported",
            )

        await room.add_message(room.OPERATOR_MEMBER_ID, content, insert_immediately=request.insert_immediately)
        if room.get_current_turn_agent_id() == room.OPERATOR_MEMBER_ID:
            await room.finish_turn(room.OPERATOR_MEMBER_ID)
        self.return_success()


class EscalateMessageToImmediateHandler(BaseHandler):
    """POST /rooms/{room_id}/messages/{msg_id}/escalate_to_immediate.json"""

    async def post(self, room_id_str: str, msg_id_str: str) -> None:
        room_id = int(room_id_str)
        db_id = int(msg_id_str)
        room = roomService.get_room(room_id)
        assertUtil.assertNotNull(room, error_message=f"room_id '{room_id}' not found", error_code="room_not_found")
        assertUtil.assertTrue(
            room.room_type == RoomType.PRIVATE,
            error_message="escalate_to_immediate is only supported in PRIVATE rooms",
            error_code="room_immediate_insert_not_supported",
        )
        await room.escalate_message_to_immediate(db_id)
        self.return_success()


# Team Room Management Handlers
class TeamRoomsHandler(BaseHandler):
    """GET /teams/{team_id}/rooms/list.json - 获取 Team 下的所有 Room"""

    async def get(self, team_id_str: str) -> None:
        team_id = int(team_id_str)
        assertUtil.assertNotNull(
            await gtTeamManager.get_team_by_id(team_id),
            error_message=f"Team ID '{team_id}' not found",
            error_code="team_not_found",
        )

        rooms = await gtRoomManager.get_rooms_by_team(team_id)
        self.return_json({"rooms": rooms})


class TeamRoomCreateHandler(BaseHandler):
    """POST /teams/{team_id}/rooms/create.json - 在 Team 下创建 Room"""

    async def post(self, team_id_str: str) -> None:
        request = self.parse_request(CreateRoomRequest)

        team_id = int(team_id_str)
        team = await gtTeamManager.get_team_by_id(team_id)
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")
        team_name = team.name

        # 检查房间是否已存在
        existing_rooms = await gtRoomManager.get_rooms_by_team(team_id)
        existing = next((r for r in existing_rooms if r.name == request.name), None)
        assertUtil.assertEqual(existing, None, error_message=f"Room '{request.name}' already exists", error_code="room_exists")
        await _assert_agent_ids_in_team(team_id, request.agent_ids)
        assertUtil.assertTrue(
            len(request.agent_ids) >= 2,
            error_message="room must have at least 2 agents",
            error_code="room_agents_too_few",
        )
        room_type = _infer_room_type_from_agent_ids(request.agent_ids)

        await gtRoomManager.save_room(GtRoom(
            team_id=team_id,
            name=request.name,
            type=room_type,
            initial_topic=request.initial_topic or "",
            max_turns=request.max_turns,
            agent_ids=list(request.agent_ids),
        ))
        await teamService.hot_reload_team(team_name)

        self.return_json({"status": "created", "room_name": request.name})


class TeamRoomDetailHandler(BaseHandler):
    """GET /teams/{team_id}/rooms/{room_id}.json - 获取指定 Room 详情"""

    async def get(self, team_id_str: str, room_id_str: str) -> None:
        team_id = int(team_id_str)
        room_id = int(room_id_str)
        assertUtil.assertNotNull(
            await gtTeamManager.get_team_by_id(team_id),
            error_message=f"Team ID '{team_id}' not found",
            error_code="team_not_found",
        )
        room = await _get_team_room_or_404(team_id, room_id)

        data = {
            "id": room.id,
            "name": room.name,
            "i18n": room.i18n or {},
            "type": room.type.name,
            "initial_topic": room.initial_topic,
            "max_turns": room.max_turns,
            "agent_ids": room.agent_ids or [],
        }
        self.return_json(data)


class TeamRoomModifyHandler(BaseHandler):
    """POST /teams/{team_id}/rooms/{room_id}/modify.json - 更新 Room"""

    async def post(self, team_id_str: str, room_id_str: str) -> None:
        request = self.parse_request(UpdateRoomRequest)

        team_id = int(team_id_str)
        room_id = int(room_id_str)
        team = await gtTeamManager.get_team_by_id(team_id)
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")
        team_name = team.name

        room = await _get_team_room_or_404(team_id, room_id)
        room_name = room.name

        room.type = RoomType(request.type)
        if request.initial_topic is not None:
            room.initial_topic = request.initial_topic
        if request.max_turns is not None:
            room.max_turns = request.max_turns

        await gtRoomManager.save_room(room)
        await teamService.hot_reload_team(team_name)

        self.return_json({"status": "updated", "room_name": room_name})


class TeamRoomDeleteHandler(BaseHandler):
    """POST /teams/{team_id}/rooms/{room_id}/delete.json - 删除 Room"""

    async def post(self, team_id_str: str, room_id_str: str) -> None:
        team_id = int(team_id_str)
        room_id = int(room_id_str)
        team = await gtTeamManager.get_team_by_id(team_id)
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")
        team_name = team.name

        room = await _get_team_room_or_404(team_id, room_id)
        room_name = room.name

        await gtRoomManager.delete_room(room_id)
        await teamService.hot_reload_team(team_name)

        self.return_json({"status": "deleted", "room_name": room_name})


class TeamRoomAgentsHandler(BaseHandler):
    """GET /teams/{team_id}/rooms/{room_id}/agents/list.json - 获取 Room Agent ID 列表"""

    async def get(self, team_id_str: str, room_id_str: str) -> None:
        team_id = int(team_id_str)
        room_id = int(room_id_str)
        assertUtil.assertNotNull(
            await gtTeamManager.get_team_by_id(team_id),
            error_message=f"Team ID '{team_id}' not found",
            error_code="team_not_found",
        )
        room = await _get_team_room_or_404(team_id, room_id)

        self.return_json({"agent_ids": room.agent_ids or []})


class TeamRoomAgentsModifyHandler(BaseHandler):
    """POST /teams/{team_id}/rooms/{room_id}/agents/modify.json - 更新 Room Agent ID 列表"""

    async def post(self, team_id_str: str, room_id_str: str) -> None:
        request = self.parse_request(UpdateAgentsRequest)

        team_id = int(team_id_str)
        room_id = int(room_id_str)
        team = await gtTeamManager.get_team_by_id(team_id)
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")
        team_name = team.name

        room = await _get_team_room_or_404(team_id, room_id)

        await _assert_agent_ids_in_team(team_id, request.agent_ids)
        await roomService.update_room_agents(room.id, request.agent_ids)
        await teamService.hot_reload_team(team_name)

        self.return_json({"status": "updated", "room_name": room.name})
