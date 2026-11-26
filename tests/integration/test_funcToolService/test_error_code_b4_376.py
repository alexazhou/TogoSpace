"""#376 B4/B5 交付标准的**逐场景实测**（真库、真工具入口，不用替身假装）。

为什么放在集成层：need_fix_4 §二 B4 列的是"**用户实际会打到的那串场景**"，
判据必须是"从工具入口进去、真的落到库里、返回体里带码"；单元层的替身只能证明分支。

每个用例都把**实际返回体**打出来（`-s` 可见），报告里的引文就是这里的输出，
不是转述——两边对不上时以本文件的实跑为准。

覆盖清单（need_fix_4 §二 B4 + §七 模块 5.1 B5）：
1. `task get 99999`                ⇒ 任务不存在
2. `agent get 9999`                ⇒ 成员不存在
3. `agent create` 用不存在的模板   ⇒ 角色模板不存在
4. `room create` 成员不足 2 人     ⇒ 房间成员不足
5. `room create` 成员不存在        ⇒ 成员不存在
6. `role update` 改 SYSTEM 模板    ⇒ SYSTEM 只读
7. `task create` 优先级非法        ⇒ 取值非法
8. `agent update --driver foo`     ⇒ driver 非法
9. `agent wake` 一个非 FAILED 成员 ⇒ `agent_not_failed` + 指路 `send_chat_msg`（B5）
"""

from __future__ import annotations

import pytest

import service.agentService as agentService
import service.ormService as ormService
import service.persistenceService as persistenceService
import service.roomService as roomService
from constants import RoleTemplateType
from dal.db import gtAgentManager, gtRoleTemplateManager, gtTeamManager
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtRoleTemplate import GtRoleTemplate
from model.dbModel.gtTeam import GtTeam
from service.funcToolService import tools
from service.roomService import ToolCallContext
from tests.base import ServiceTestCase

TEAM = "b4_codes_376"


class TestB4Codes(ServiceTestCase):
    @classmethod
    async def async_setup_class(cls) -> None:
        db_path = cls._get_test_db_path()
        await ormService.startup(db_path)
        await persistenceService.startup()
        await agentService.startup()
        await roomService.startup()
        team = await gtTeamManager.save_team(GtTeam(name=TEAM))
        cls.team_id = team.id
        await gtAgentManager.batch_save_agents(
            team.id,
            [
                GtAgent(team_id=team.id, name="alice", role_template_id=0),
                GtAgent(team_id=team.id, name="bob", role_template_id=0),
            ],
        )
        await gtRoleTemplateManager.save_role_template(
            GtRoleTemplate(name="built_in_system_376", soul="built in", type=RoleTemplateType.SYSTEM)
        )

    @classmethod
    async def async_teardown_class(cls) -> None:
        await agentService.shutdown()
        roomService.shutdown()
        await persistenceService.shutdown()
        await ormService.shutdown()

    def _ctx(self) -> ToolCallContext:
        return ToolCallContext(agent_id=1, team_id=self.team_id, tool_name="probe")

    # -- 1. 任务不存在 ---------------------------------------------------- #
    async def test_task_get_missing_returns_not_found(self) -> None:
        result = await tools.get_task_detail(task_id=99999, _context=self._ctx())
        print(f"\n[task get 99999] {result}")

        assert result["success"] is False
        assert result["error_code"] == "not_found"
        assert "任务不存在" in result["message"]

    # -- 2. 成员不存在 ---------------------------------------------------- #
    async def test_agent_get_missing_returns_not_found(self) -> None:
        result = await tools.get_agent_info(agent_id=9999, _context=self._ctx())
        print(f"\n[agent get 9999] {result}")

        assert result["success"] is False
        assert result["error_code"] == "not_found"
        assert "未找到成员" in result["message"]

    # -- 3. 角色模板不存在 ------------------------------------------------ #
    async def test_agent_create_with_missing_template_returns_not_found(self) -> None:
        result = await tools.create_agent(
            name="carol_376", role_template_name="不存在的模板_376", _context=self._ctx()
        )
        print(f"\n[agent create 用不存在模板] {result}")

        assert result["success"] is False
        assert result["error_code"] == "not_found"
        assert "未找到角色模板" in result["message"]

    # -- 4. 房间成员不足 2 人 -------------------------------------------- #
    async def test_room_create_with_one_member_returns_invalid_state(self) -> None:
        result = await tools.create_room(name="solo_room_376", member_names=["alice"], _context=self._ctx())
        print(f"\n[room create 只有 1 人] {result}")

        assert result["success"] is False
        # 房间能被创建，卡在"成员不足"——是**状态**不允许，不是参数错、也不是不存在。
        assert result["error_code"] == "invalid_state"
        assert "房间成员不足 2 人" in result["message"]

    # -- 5. 房间成员不存在 ------------------------------------------------ #
    async def test_room_create_with_missing_member_returns_not_found(self) -> None:
        result = await tools.create_room(
            name="ghost_room_376", member_names=["alice", "nobody_376"], _context=self._ctx()
        )
        print(f"\n[room create 含不存在成员] {result}")

        assert result["success"] is False
        assert result["error_code"] == "not_found"
        assert "未找到成员" in result["message"]

    # -- 6. SYSTEM 模板只读 ---------------------------------------------- #
    async def test_role_update_system_template_returns_permission_denied(self) -> None:
        result = await tools.update_role_template(
            name="built_in_system_376", type="SYSTEM", soul="hacked", _context=self._ctx()
        )
        print(f"\n[role update 改 SYSTEM] {result}")

        assert result["success"] is False
        # SYSTEM 模板**存在**，只是不许改 ⇒ permission_denied（不是 not_found）。
        assert result["error_code"] == "permission_denied"
        assert "SYSTEM 角色模板" in result["message"]

    # -- 7. 优先级非法 ---------------------------------------------------- #
    async def test_task_create_invalid_priority_returns_invalid_tool_args(self) -> None:
        # 说明：模型面（经 `run_tool_call`）会先被 schema 的 `Literal` 挡掉，同样得
        # `invalid_tool_args`；这里**直接打工具函数**要走服务层的兜底校验，
        # 两条路都收敛到同一个码，才是 B4 想要的效果。
        result = await tools.create_task(
            title="非法优先级_376", assignee_id=1, priority="URGENT", _context=self._ctx()
        )
        print(f"\n[task create priority=URGENT] {result}")

        assert result["success"] is False
        assert result["error_code"] == "invalid_tool_args"
        assert "无效的优先级" in result["message"]

    # -- 8. driver 非法 --------------------------------------------------- #
    async def test_agent_update_invalid_driver_returns_invalid_tool_args(self) -> None:
        agent = (await gtAgentManager.get_team_agents_by_names(self.team_id, ["alice"]))[0]
        result = await tools.update_agent(agent_id=agent.id, driver="foo", _context=self._ctx())
        print(f"\n[agent update --driver foo] {result}")

        assert result["success"] is False
        assert result["error_code"] == "invalid_tool_args"
        assert "driver 只允许 native" in result["message"]

    # -- 9. B5：wake 一个非 FAILED 成员 ---------------------------------- #
    async def test_wake_non_failed_agent_returns_agent_not_failed(self, monkeypatch) -> None:
        # 运行时 agent 由 `agentService._agents` 提供（需要一个真的跑起来的消费循环），
        # 本用例只判"返回体"，故用替身给出一个 IDLE 成员；库里的 alice/bob 仍在，
        # 这里不碰它们。
        from types import SimpleNamespace

        from constants import AgentStatus

        idle = SimpleNamespace(
            gt_agent=SimpleNamespace(name="idle_376"),
            status=AgentStatus.IDLE,
            start_consumer_task=lambda: pytest.fail("非 FAILED 成员不许被唤醒"),
        )
        monkeypatch.setattr(agentService, "get_team_agents", lambda team_id: [idle])

        result = await tools.wake_up_agent(agent_name="idle_376", _context=self._ctx())
        print(f"\n[agent wake bob（非 FAILED）] {result}")

        # B5 的**三条**都在这里：仍是失败、有码、文案指路 send_chat_msg。
        assert result["success"] is False, "B5 明确要求保持 success=False（改 True 会误导模型）"
        assert result["error_code"] == "agent_not_failed"
        assert "send_chat_msg" in result["message"], "文案必须指路 send_chat_msg"
        assert "FAILED" in result["message"], "必须说明 wake 只对 FAILED 生效"
