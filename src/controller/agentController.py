from typing import Optional

from pydantic import BaseModel, Field

from constants import DriverType, AgentStatus
from controller.baseController import BaseHandler
from dal.db import gtTeamManager, gtAgentManager, gtRoleTemplateManager, gtAgentTaskManager
from service.agentService.toolRegistry import validate_tool_allow_specs
from model.coreModel import AgentRuntimeInfo
from model.dbModel.gtAgent import GtAgent
from model.coreModel.memoryModel import memory_api_dict
from service import teamService, agentService, taskService, roomService, memoryService
from service.agentService.compact import CompactInProgressError, NoCompactableHistoryError
from util import assertUtil


class AgentSaveItem(BaseModel):
    """Agent 保存项：id 可选，有则更新，无则创建。"""
    id: Optional[int] = None
    name: str
    role_template_id: int
    model: str = ""
    driver: DriverType = DriverType.NATIVE
    allow_tools: list[str] | None = None
    allow_skills: list[str] | None = None


class AgentsSaveRequest(BaseModel):
    """全量覆盖 Agent 列表请求。"""
    agents: list[AgentSaveItem]



class AgentListInfo(BaseModel):
    """Agent 列表接口的响应外壳（控制器层契约）。"""

    agents: list[AgentRuntimeInfo] = Field(default_factory=list)


class AgentListHandler(BaseHandler):
    """GET /agents/list.json?team_id=<id> - 获取 team 的成员配置列表"""

    async def get(self):
        team_id_raw = self.get_query_argument("team_id", None)
        include_special_raw = self.get_query_argument("include_special", "false")
        if not team_id_raw:
            self.return_json(AgentListInfo())
            return

        team_id = int(team_id_raw)
        team = await gtTeamManager.get_team_by_id(team_id)
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")

        include_special = include_special_raw.strip().lower() in {"1", "true", "yes", "on"}
        agents = await agentService.get_team_agent_list_info(team.id, include_special=include_special)
        self.return_json(AgentListInfo(agents=agents))


class TeamAgentsSaveHandler(BaseHandler):
    """PUT /teams/<id>/agents/save.json - 全量覆盖 Agent 列表"""

    async def put(self, team_id_str: str) -> None:
        team_id = int(team_id_str)
        team = await gtTeamManager.get_team_by_id(team_id)
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")

        request = self.parse_request(AgentsSaveRequest)

        request_ids = [a.id for a in request.agents if a.id is not None]
        existing_agents = await gtAgentManager.get_team_all_agents(team_id)
        existing_ids = {a.id for a in existing_agents}

        invalid_ids = [id_ for id_ in request_ids if id_ not in existing_ids]
        assertUtil.assertEqual(
            len(invalid_ids), 0,
            error_message=f"Agent ID 不存在于当前 team: {invalid_ids}",
            error_code="agent_not_found",
        )

        final_names = [m.name for m in request.agents]
        duplicate_names = [n for n in final_names if final_names.count(n) > 1]
        assertUtil.assertEqual(
            len(duplicate_names), 0,
            error_message=f"agent name 重复: {duplicate_names}",
            error_code="duplicate_agent_name",
        )

        for item in request.agents:
            error_msg = validate_tool_allow_specs(item.allow_tools or [])
            assertUtil.assertEqual(error_msg, None, error_message=error_msg or "", error_code="invalid_tool_allow_specs")

        _tpl_ids = list({a.role_template_id for a in request.agents})
        _fetched = await gtRoleTemplateManager.get_role_templates_by_ids(_tpl_ids)
        assertUtil.assertEqual(len(_fetched), len(_tpl_ids), error_message="部分角色模板不存在", error_code="role_template_not_found")
        updated_agents = await agentService.overwrite_team_agents(
            team_id,
            [
                GtAgent(
                    id=item.id,
                    team_id=team_id,
                    name=item.name,
                    role_template_id=item.role_template_id,
                    model=item.model,
                    driver=item.driver,
                    allow_tools=item.allow_tools,
                    allow_skills=item.allow_skills,
                )
                for item in request.agents
            ],
        )

        await teamService.hot_reload_team(team.name)

        self.return_json({
            "status": "ok",
            "agents": [
                {
                    "id": agent.id,
                    "name": agent.name,
                    "i18n": agent.i18n or {},
                    "employee_number": agent.employee_number,
                    "role_template_id": agent.role_template_id,
                    "model": agent.model,
                    "driver": agent.driver.value,
                    "allow_skills": agent.allow_skills,
                }
                for agent in updated_agents
            ],
        })


class AgentDetailHandler(BaseHandler):
    """GET /teams/<id>/agents/<name>.json - 获取单个成员配置详情"""

    async def get(self, team_id_str: str, agent_name: str) -> None:
        team_id = int(team_id_str)
        team = await gtTeamManager.get_team_by_id(team_id)
        assertUtil.assertNotNull(team, error_message=f"Team ID '{team_id}' not found", error_code="team_not_found")

        agent = await gtAgentManager.get_agent(team_id, agent_name)
        assertUtil.assertNotNull(
            agent,
            error_message=f"Agent '{agent_name}' not found in team '{team.name}'",
            error_code="agent_not_found",
        )

        self.return_json(await agentService.get_agent_detail_info(agent))


class AgentDetailByIdHandler(BaseHandler):
    """GET /agents/<id>.json - 获取单个成员配置详情"""

    async def get(self, agent_id_str: str) -> None:
        agent_id = int(agent_id_str)
        agents = await gtAgentManager.get_agents_by_ids([agent_id])
        agent = agents[0] if agents else None
        assertUtil.assertNotNull(
            agent,
            error_message=f"Agent ID '{agent_id}' not found",
            error_code="agent_not_found",
        )
        self.return_json(await agentService.get_agent_detail_info(agent))


class AgentContainersHandler(BaseHandler):
    """GET /agents/<id>/containers.json - 查询指定 Agent 的自建容器列表"""

    async def get(self, agent_id_str: str) -> None:
        agent_id = int(agent_id_str)
        agents = await gtAgentManager.get_agents_by_ids([agent_id])
        agent = agents[0] if agents else None
        assertUtil.assertNotNull(
            agent,
            error_message=f"Agent ID '{agent_id}' not found",
            error_code="agent_not_found",
        )
        self.return_json(await agentService.get_agent_containers(agent.id))


class AgentErrorMessageHandler(BaseHandler):
    """GET /agents/<id>/error_message.json - 查询 Agent 未完成任务的失败信息"""

    async def get(self, agent_id_str: str) -> None:
        agent_id = int(agent_id_str)
        agents = await gtAgentManager.get_agents_by_ids([agent_id])
        agent = agents[0] if agents else None
        assertUtil.assertNotNull(
            agent,
            error_message=f"Agent ID '{agent_id}' not found",
            error_code="agent_not_found",
        )
        error_message = await agentService.get_agent_error_message(agent.id)
        self.return_json({"error_message": error_message})


class AgentTasksHandler(BaseHandler):
    """GET /agents/<id>/tasks.json - 获取指定 Agent 的协作任务列表"""

    async def get(self, agent_id_str: str) -> None:
        agent_id = int(agent_id_str)
        limit_raw = self.get_query_argument("limit", "30")
        include_closed_raw = self.get_query_argument("include_closed", "false")
        limit = max(1, min(int(limit_raw), 100))
        include_closed = include_closed_raw.strip().lower() in {"1", "true", "yes", "on"}

        agents = await gtAgentManager.get_agents_by_ids([agent_id])
        agent = agents[0] if agents else None
        assertUtil.assertNotNull(
            agent,
            error_message=f"Agent ID '{agent_id}' not found",
            error_code="agent_not_found",
        )

        self.return_json(
            await taskService.list_tasks(
                team_id=agent.team_id,
                assignee_id=agent.id,
                open_only=not include_closed,
                limit=limit,
                # Agent 任务卡看「最近改过的在最上面」，与团队级列表的优先级优先区分开。
                order=gtAgentTaskManager.ORDER_UPDATED_DESC,
            )
        )


class AgentMemoriesHandler(BaseHandler):
    """GET /agents/<id>/memories.json - 获取指定 Agent 的长期记忆列表（只读）"""

    async def get(self, agent_id_str: str) -> None:
        agent_id = int(agent_id_str)
        include_archived_raw = self.get_query_argument("include_archived", "false")
        include_archived = include_archived_raw.strip().lower() in {"1", "true", "yes", "on"}

        agents = await gtAgentManager.get_agents_by_ids([agent_id])
        agent = agents[0] if agents else None
        assertUtil.assertNotNull(
            agent,
            error_message=f"Agent ID '{agent_id}' not found",
            error_code="agent_not_found",
        )

        memories = await memoryService.get_agent_memories_for_view(
            agent.team_id, agent.id, include_archived=include_archived,
        )
        # 枚举对外必须是小写 value：`GtAgentMemory.to_json()` 会输出枚举 name
        # （`SUMMARY` / `ACTIVE`），与前端契约不符，统一走 `memory_api_dict` 覆写。
        self.return_json({"memories": [memory_api_dict(memory) for memory in memories]})


class TeamTasksHandler(BaseHandler):
    """GET /teams/<id>/tasks.json - 获取指定 Team 的协作任务列表"""

    async def get(self, team_id_str: str) -> None:
        team_id = int(team_id_str)
        limit_raw = self.get_query_argument("limit", "500")
        include_closed_raw = self.get_query_argument("include_closed", "false")
        limit = max(1, min(int(limit_raw), 1000))
        include_closed = include_closed_raw.strip().lower() in {"1", "true", "yes", "on"}

        team = await gtTeamManager.get_team_by_id(team_id)
        assertUtil.assertNotNull(
            team,
            error_message=f"Team ID '{team_id}' not found",
            error_code="team_not_found",
        )

        self.return_json(
            await taskService.list_tasks(
                team_id=team.id,
                open_only=not include_closed,
                limit=limit,
            )
        )


class AgentResumeHandler(BaseHandler):
    """POST /agents/<agent_id>/resume.json - 对 FAILED 状态的 Agent 触发续跑"""

    async def post(self, agent_id_str: str) -> None:
        agent_id = int(agent_id_str)
        agent = agentService.get_agent_or_none(agent_id)
        assertUtil.assertNotNull(agent, None, f"运行时 Agent ID '{agent_id}' 不存在", "agent_not_found")
        assertUtil.assertTrue(agent.status == AgentStatus.FAILED, None, f"Agent ID={agent.gt_agent.id} 当前状态不是 FAILED（当前: {agent.status.name}）", "agent_not_failed")

        agent.start_consumer_task()

        self.return_json({"status": "resumed", "agent_id": agent.gt_agent.id})


class AgentClearDataHandler(BaseHandler):
    """POST /agents/{id}/clear_data.json - 清除指定 Agent 的历史记录"""

    async def post(self, agent_id_str: str) -> None:
        agent_id = int(agent_id_str)
        agent = await gtAgentManager.get_agent_by_id(agent_id)
        assertUtil.assertNotNull(agent, error_message=f"Agent ID '{agent_id}' not found", error_code="agent_not_found")

        result = await teamService.clear_agent_data(agent_id)

        self.return_json({
            "status": "cleared",
            "agent_id": agent_id,
            "deleted": result,
        })


class AgentStopHandler(BaseHandler):
    """POST /agents/<agent_id>/stop.json - 人工停止 ACTIVE 状态的 Agent 当前 turn"""

    async def post(self, agent_id_str: str) -> None:
        agent_id = int(agent_id_str)
        agent = agentService.get_agent_or_none(agent_id)
        assertUtil.assertNotNull(agent, None, f"运行时 Agent ID '{agent_id}' 不存在", "agent_not_found")
        assertUtil.assertTrue(agent.status == AgentStatus.ACTIVE, None, f"Agent ID={agent.gt_agent.id} 当前状态不是 ACTIVE（当前: {agent.status.name}）", "agent_not_active")

        agent.cancel_current_turn()

        self.return_json({"status": "stopped", "agent_id": agent.gt_agent.id})


class AgentCompactHandler(BaseHandler):
    """POST /agents/<agent_id>/compact.json - 手动触发上下文压缩（Compact）

    错误映射（**前四种是业务拒绝转 400，其余异常必须仍是 500**）：

    | 情形 | 状态码 | error_code |
    |---|---|---|
    | Agent 未装载进内存 | 400 | `agent_not_loaded` |
    | Agent 正在执行（ACTIVE） | 400 | `agent_is_active` |
    | 已有一次手动压缩在进行 | 400 | `compact_in_progress` |
    | 无可压缩历史 | 400 | `no_compactable_history` |
    | LLM 失败 / 摘要为空 / 其他 | 500 | （原样透出） |

    没有 `agent_not_found` 这一行：运行时「不存在」与「未装载」**不可区分**——
    两者都查内存 `_agents`，`get_agent_or_none` 都返回 None。补一条 SQL 查库的
    判断只会造出一个永远进不去的假分支，所以统一归到 `agent_not_loaded`。

    「无可压缩历史」走 `NoCompactableHistoryError` 类型判断而不是匹配异常文案：
    文案改一个字就静默失效，而它决定的是「400 业务提示」还是「500 故障」，
    这个分支不该建在错误字符串上。`CompactInProgressError` 同理。

    副作用须知：`_execute_compact` 在判定「无可压缩」前**已经写了一条 COMPACT
    活动并置 FAILED**（`agentTurnRunner.py` 里 add_activity 在前、build_plan 在后）。
    这条活动保留不动——它准确记录了「用户点了压缩、系统判定没得压」，
    删掉反而让面板少一条真实轨迹。

    **压缩不可取消**：手动压缩全程不碰 `status`（保持 IDLE），所以此刻点「停止」
    会拿到 `agent_not_active`——如实回答（确实没有 turn 可取消），不是回归。
    中途打断会留下「摘要已插库但内存未裁剪」的半成品，比让它跑完更危险；
    本单不提供取消能力，前端按钮在压缩期间禁用。
    """

    async def post(self, agent_id_str: str) -> None:
        agent_id = int(agent_id_str)

        runtime_agent = agentService.get_agent_or_none(agent_id)
        assertUtil.assertNotNull(
            runtime_agent, None,
            f"运行时 Agent ID '{agent_id}' 不存在（Agent 未装载进内存）",
            "agent_not_loaded",
        )

        # ACTIVE 期间改 _history 会与进行中的 turn 抢同一份历史。
        #
        # ⚠️ 这个检查**不是**并发防护的全部：`status` 不是锁，它只是个标签
        # （`consume()` 进来无脑 `_set_status(ACTIVE)`，而 `_set_status` 在值相同时
        # 直接 return）。真正的互斥由 `_compacting` 承担——`Agent.compact_context()`
        # 进门同步占用，`consume()` 取 job 前等它归位。
        #
        # 本检查只是把「turn 正在跑」这条最常见的情形挡在更早的地方，
        # 少走一次无效的协程进入。
        assertUtil.assertTrue(
            runtime_agent.status != AgentStatus.ACTIVE, None,
            f"Agent ID={agent_id} 正在执行任务，暂不支持手动压缩（当前: {runtime_agent.status.name}）",
            "agent_is_active",
        )

        # 已有一次手动压缩在跑：直接拒绝，**不排队**。
        # 排队会把「用户连点两次」变成「串行压两次」——防并发变成串行执行，
        # 用户仍然得到两次压缩。这里同时兜住「检查→占用」被将来的 await 破坏
        # 的情况：`compact_context()` 内部占用失败也会抛同一个异常。
        assertUtil.assertTrue(
            not runtime_agent.is_compacting, None,
            f"Agent ID={agent_id} 已有一次手动压缩正在进行，请稍候",
            "compact_in_progress",
        )

        try:
            usage = await runtime_agent.compact_context()
        except CompactInProgressError:
            # 兜住「预检通过但占用失败」的窄窗口（见上一条注释）。
            assertUtil.assertTrue(
                False, None,
                f"Agent ID={agent_id} 已有一次手动压缩正在进行，请稍候",
                "compact_in_progress",
            )
            return
        except NoCompactableHistoryError:
            # 业务拒绝：不是故障，用户点了按钮但没有足够历史可压。
            assertUtil.assertTrue(
                False, None,
                "当前无足够可压缩的历史消息",
                "no_compactable_history",
            )
            return

        self.return_json({
            "success": True,
            "agent_id": agent_id,
            "context_usage": usage.model_dump(mode="json") if usage is not None else None,
        })


class AgentModifyPropertiesRequest(BaseModel):
    model: str | None = None
    allow_tools: list[str] | None = None
    allow_skills: list[str] | None = None


class AgentModifyPropertiesHandler(BaseHandler):
    """POST /agents/<agent_id>/modify_properties.json - 修改 Agent 的运行时属性 (allow_tools, allow_skills)"""

    async def post(self, agent_id_str: str) -> None:
        agent_id = int(agent_id_str)
        agent = await gtAgentManager.get_agent_by_id(agent_id)
        assertUtil.assertNotNull(agent, error_message=f"Agent ID '{agent_id}' not found", error_code="agent_not_found")

        request = self.parse_request(AgentModifyPropertiesRequest)

        if "model" in request.model_fields_set:
            agent.model = request.model or ""

        if "allow_tools" in request.model_fields_set:
            if request.allow_tools is not None:
                error_msg = validate_tool_allow_specs(request.allow_tools)
                assertUtil.assertEqual(error_msg, None, error_message=error_msg or "", error_code="invalid_tool_allow_specs")
            agent.allow_tools = request.allow_tools
        
        if "allow_skills" in request.model_fields_set:
            agent.allow_skills = request.allow_skills

        await gtAgentManager.save_agent(agent)

        await agentService.hot_reload_agent(agent_id)

        self.return_json(await agentService.get_agent_detail_info(agent))


class AgentControlRoomHandler(BaseHandler):
    """GET /agents/<agent_id>/control_room.json - 获取（或创建）指定 Agent 的私聊控制房间"""

    async def get(self, agent_id_str: str) -> None:
        agent_id = int(agent_id_str)
        agent = await gtAgentManager.get_agent_by_id(agent_id)
        assertUtil.assertNotNull(agent, error_message=f"Agent ID '{agent_id}' not found", error_code="agent_not_found")
        
        room, created = await roomService.get_or_create_control_room(agent.team_id, agent_id)
        
        self.return_json({
            "room_id": room.room_id,
            "created": created,
        })

