"""#196 P1 单元测试：定位统一 / PATCH 语义 / 删除可预演。

分层口径（为什么这些用 DAL 替身而不是真库）：
- **真库**能验的是「落库后确实是这个状态」，已在 `tests/integration/test_funcToolService/`
  的既有用例里覆盖（`test_console_tool.py` 的 CC1 PATCH 段、`test_save_room_members.py`）；
- 这里要验的是**分支选择本身**：省略 vs 给值 vs clear 三条路各自走到哪个落值表达式、
  dry_run 有没有真的跳过写入调用。这些用替身能**直接断言「某个下游函数没被调用」**，
  真库里「没写库」只能靠前后状态比，容易被同值写入掩盖。

三组判据与 draft §2.1 / §2.2 / §2.3 一一对应：

## P1a 定位统一
1. id 与 name 同时给 ⇒ 以 id 为准（并且 name 不参与查询）；
2. 显式空串 **不等于** 省略（省略 = 返回全部，空串 = 一个不存在的名字）；
3. 未找到时错误文案带**实际使用的定位值**（`id=7` / `name=xxx`，不是两个都报）；
4. `get_agent_info` 按 id 查不到时有**三种**不同原因，分别报不同的码
   （库里无 / 离职 / 在职但运行时未加载）。

## P1b PATCH
5. 省略 = 保持现值（四个 save 各一条），**且不触发改名副作用**；
6. `clear_fields` = 恢复默认，落库形态逐字段钉住（`""` / `None`）；
7. 同字段既给值又 clear ⇒ `ambiguous_clear_field`，**且不改库**；
8. 白名单之外 / 非法类型 ⇒ `invalid_clear_field`；
9. 新建时必填项在**运行时**补齐（schema 已放开，见报告 §「save 系列 required 放开」）。

## P1c dry_run
10. `dry_run=True` 时服务层**不调用写入函数**（零副作用），但仍跑完全部校验；
11. 工具层返回 `would_delete` 清单；真删返回 `deleted` 清单。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

import service.agentService as agentService
import service.deptService as deptService
import service.roomService as roomService
import service.roomService.core as roomCore
from constants import DriverType, EmployStatus, RoomType
from model.dbModel.gtAgent import GtAgent
from dal.db import gtScheduleJobManager as dalDb_gtScheduleJobManager
from model.dbModel.gtDept import GtDept
from service.funcToolService import tools
from service.roomService import ToolCallContext

TEAM_ID = 1


@pytest.fixture(autouse=True)
def _language_stub(monkeypatch):
    """`_serialize_dept_node` 会读 `configUtil.get_language()`（依赖全局 AppConfig）。

    本文件只测分支选择，不测 i18n 渲染；把语言固定成 zh-CN，避免「为了取语言
    去 load 一份完整配置」把单元测试拖成集成测试。
    """
    monkeypatch.setattr(tools.configUtil, "get_language", lambda: "zh-CN")
    yield


def _ctx(tool_name: str = "t") -> ToolCallContext:
    return ToolCallContext(agent_id=1, team_id=TEAM_ID, tool_name=tool_name)


def _agent(agent_id: int, name: str, *, model: str = "", driver=DriverType.NATIVE) -> GtAgent:
    return GtAgent(
        id=agent_id,
        team_id=TEAM_ID,
        name=name,
        role_template_id=21,
        employ_status=EmployStatus.ON_BOARD,
        model=model,
        driver=driver,
        i18n={},
    )


def _dept(dept_id: int, name: str, *, parent_id: int | None = None, manager_id: int = 1,
          agent_ids: list[int] | None = None) -> GtDept:
    return GtDept(
        id=dept_id,
        team_id=TEAM_ID,
        name=name,
        responsibility=f"{name} 的职责",
        parent_id=parent_id,
        manager_id=manager_id,
        agent_ids=agent_ids if agent_ids is not None else [manager_id],
        i18n={},
        children=[],
    )


# =========================================================================== #
# P1a：定位统一
# =========================================================================== #


class TestP1aDeptLocator:
    """`get_dept_info(dept_id | name)`：id 优先、空串 ≠ 省略、文案带定位值。"""

    def _patch_tree(self, monkeypatch, root: GtDept) -> None:
        monkeypatch.setattr(deptService, "get_dept_tree", AsyncMock(return_value=root))
        monkeypatch.setattr(
            tools.gtAgentManager, "get_team_all_agents",
            AsyncMock(return_value=[_agent(1, "alice"), _agent(2, "bob")]),
        )

    @pytest.mark.asyncio
    async def test_id_wins_over_name(self, monkeypatch):
        """同时给 id 和 name ⇒ 只用 id 查，name 不参与（也不因为 name 对不上而报错）。"""
        root = _dept(1, "root", manager_id=1)
        child = _dept(2, "child", parent_id=1, manager_id=2)
        root.children = [child]
        self._patch_tree(monkeypatch, root)

        result = await tools.get_dept_info(dept_id=2, name="root", _context=_ctx())

        assert result["success"] is True, result
        assert result["dept"]["dept_id"] == 2

    @pytest.mark.asyncio
    async def test_lookup_by_internal_name(self, monkeypatch):
        """`name` 比对的是**内部标识**，不是 i18n 展示名（展示名会随语言变，不能当定位键）。"""
        root = _dept(1, "root", manager_id=1)
        child = _dept(2, "research", parent_id=1, manager_id=2)
        child.i18n = {"dept_name": {"zh-CN": "研发部", "en": "R&D"}}
        root.children = [child]
        self._patch_tree(monkeypatch, root)

        by_internal = await tools.get_dept_info(name="research", _context=_ctx())
        by_display = await tools.get_dept_info(name="研发部", _context=_ctx())

        assert by_internal["success"] is True
        assert by_internal["dept"]["dept_id"] == 2
        assert by_display["success"] is False
        assert by_display["message"] == "未找到部门: name=研发部"

    @pytest.mark.asyncio
    async def test_empty_string_is_not_omitted(self, monkeypatch):
        """显式空串 ≠ 省略：省略返回全树，空串是一个不存在的名字。"""
        root = _dept(1, "root", manager_id=1)
        self._patch_tree(monkeypatch, root)

        omitted = await tools.get_dept_info(_context=_ctx())
        empty = await tools.get_dept_info(name="", _context=_ctx())

        assert omitted["success"] is True
        assert empty["success"] is False
        assert empty["message"] == "未找到部门: name="

    @pytest.mark.asyncio
    async def test_not_found_message_carries_only_the_used_locator(self, monkeypatch):
        """未找到时报**实际用过的**定位值：给 id 只报 id（报两个会让人以为两个都没命中）。"""
        self._patch_tree(monkeypatch, _dept(1, "root", manager_id=1))

        by_id = await tools.get_dept_info(dept_id=99, _context=_ctx())
        by_name = await tools.get_dept_info(name="ghost", _context=_ctx())

        assert by_id["message"] == "未找到部门: id=99"
        assert by_name["message"] == "未找到部门: name=ghost"


class TestP1aRoomLocator:
    """`get_room_info(room_id | name)`。"""

    def _patch(self, monkeypatch, rooms: list[SimpleNamespace]) -> None:
        async def _by_id(room_id: int):
            return next((r for r in rooms if r.id == room_id), None)

        async def _by_name(team_id: int, name: str):
            return next((r for r in rooms if r.name == name), None)

        monkeypatch.setattr(tools.gtRoomManager, "get_rooms_by_team", AsyncMock(return_value=rooms))
        monkeypatch.setattr(tools.gtRoomManager, "get_room_by_id", AsyncMock(side_effect=_by_id))
        monkeypatch.setattr(tools.gtRoomManager, "get_room_by_team_and_name", AsyncMock(side_effect=_by_name))
        monkeypatch.setattr(roomService, "get_room", lambda room_id: None)
        monkeypatch.setattr(
            tools.gtAgentManager, "get_team_all_agents",
            AsyncMock(return_value=[_agent(1, "alice"), _agent(2, "bob")]),
        )

    def _room(self, room_id: int, name: str) -> SimpleNamespace:
        return SimpleNamespace(
            id=room_id, team_id=TEAM_ID, name=name, type=RoomType.PRIVATE,
            agent_ids=[1, 2], tags=[], initial_topic="", max_rounds=None, i18n={},
        )

    @pytest.mark.asyncio
    async def test_id_wins_and_cross_team_id_is_not_found(self, monkeypatch):
        """id 优先；别的团队的房间 id 必须当作「未找到」，不能返回别人的房间。"""
        own = self._room(5, "own_room")
        foreign = SimpleNamespace(**{**vars(self._room(6, "foreign_room")), "team_id": 999})
        self._patch(monkeypatch, [own, foreign])

        hit = await tools.get_room_info(room_id=5, name="nope", _context=_ctx())
        cross = await tools.get_room_info(room_id=6, _context=_ctx())

        assert hit["success"] is True
        assert hit["room"]["room_name"] == "own_room"
        assert cross["success"] is False
        assert cross["message"] == "未找到房间: id=6"

    @pytest.mark.asyncio
    async def test_empty_string_is_not_omitted(self, monkeypatch):
        self._patch(monkeypatch, [self._room(5, "own_room")])

        omitted = await tools.get_room_info(_context=_ctx())
        empty = await tools.get_room_info(name="", _context=_ctx())

        assert omitted["success"] is True and len(omitted["rooms"]) == 1
        assert empty["success"] is False
        assert empty["message"] == "未找到房间: name="


class TestP1aAgentLocator:
    """`get_agent_info(agent_id | name)`：按 id 查不到时分三种原因报。"""

    def _patch(self, monkeypatch, *, runtime: list[Any], rows: list[GtAgent]) -> None:
        async def _by_id(agent_id: int):
            return next((r for r in rows if r.id == agent_id), None)

        monkeypatch.setattr(agentService, "get_team_agents", lambda team_id: runtime)
        monkeypatch.setattr(tools.gtAgentManager, "get_agent_by_id", AsyncMock(side_effect=_by_id))
        monkeypatch.setattr(deptService, "get_agent_depts", AsyncMock(return_value=[]))
        monkeypatch.setattr(roomService, "get_all_rooms", lambda: [])
        monkeypatch.setattr(
            dalDb_gtScheduleJobManager, "get_first_unfinished_job", AsyncMock(return_value=None)
        )

    @staticmethod
    def _runtime(agent: GtAgent):
        from constants import AgentStatus

        return SimpleNamespace(gt_agent=agent, status=AgentStatus.IDLE)

    @pytest.mark.asyncio
    async def test_lookup_by_runtime_name_and_display_name(self, monkeypatch):
        """`name` 兼容内部 name 与 display_name（改造前的既有行为，不得收窄）。"""
        alice = _agent(11, "alice")
        alice.i18n = {"display_name": {"zh-CN": "爱丽丝", "en": "Alice"}}
        self._patch(monkeypatch, runtime=[self._runtime(alice)], rows=[alice])

        by_name = await tools.get_agent_info(name="alice", _context=_ctx())
        by_display = await tools.get_agent_info(name="爱丽丝", _context=_ctx())

        assert by_name["success"] is True and by_name["agent"]["id"] == 11
        assert by_display["success"] is True and by_display["agent"]["id"] == 11

    @pytest.mark.asyncio
    async def test_id_not_in_db(self, monkeypatch):
        """① 库里根本没有这个 id ⇒ 普通「未找到」，不带 error_code。"""
        self._patch(monkeypatch, runtime=[], rows=[])

        result = await tools.get_agent_info(agent_id=99, _context=_ctx())

        assert result["success"] is False
        assert "error_code" not in result
        assert result["message"] == "未找到成员: id=99"

    @pytest.mark.asyncio
    async def test_id_off_board(self, monkeypatch):
        """② 库里有但已离职 ⇒ `member_not_on_board`（不是「未找到」——人明明存在）。"""
        gone = _agent(12, "gone")
        gone.employ_status = EmployStatus.OFF_BOARD
        self._patch(monkeypatch, runtime=[], rows=[gone])

        result = await tools.get_agent_info(agent_id=12, _context=_ctx())

        assert result["success"] is False
        assert result["error_code"] == "member_not_on_board"
        assert "id=12" in result["message"]

    @pytest.mark.asyncio
    async def test_id_on_board_but_not_loaded(self, monkeypatch):
        """③ 在职但运行时未加载 ⇒ `agent_runtime_not_loaded`，文案指向 reload。

        这条与 ② 必须**分开报**：两者的处置动作不同（一个要恢复在职，一个只要 reload），
        合并成一句「未找到」会让排障方向反掉。
        """
        fresh = _agent(13, "fresh")
        self._patch(monkeypatch, runtime=[], rows=[fresh])

        result = await tools.get_agent_info(agent_id=13, _context=_ctx())

        assert result["success"] is False
        assert result["error_code"] == "agent_runtime_not_loaded"
        assert "reload_team" in result["message"]

    @pytest.mark.asyncio
    async def test_empty_string_is_not_omitted(self, monkeypatch):
        self._patch(monkeypatch, runtime=[self._runtime(_agent(11, "alice"))], rows=[_agent(11, "alice")])

        result = await tools.get_agent_info(name="", _context=_ctx())

        assert result["success"] is False
        assert result["message"] == "未找到成员: name="


# =========================================================================== #
# P1b：PATCH 语义
# =========================================================================== #


class TestP1bSaveAgentPatch:
    """`save_agent` 的省略 / 给值 / clear 三条路。"""

    def _patch(self, monkeypatch, agent: GtAgent) -> None:
        monkeypatch.setattr(tools.gtAgentManager, "get_agent_by_id", AsyncMock(return_value=agent))
        monkeypatch.setattr(tools.gtAgentManager, "get_agent", AsyncMock(return_value=agent))
        monkeypatch.setattr(tools.gtAgentManager, "batch_save_agents", AsyncMock())
        monkeypatch.setattr(
            tools.gtRoleTemplateManager, "get_role_template_by_name",
            AsyncMock(return_value=SimpleNamespace(id=21, name="writer")),
        )
        monkeypatch.setattr(
            tools.gtRoleTemplateManager, "get_role_template_by_id",
            AsyncMock(return_value=SimpleNamespace(id=21, name="writer")),
        )
        # 改名路径的副作用：任何一次不该发生的调用都会当场炸掉用例。
        monkeypatch.setattr(
            tools.containerService, "delete_agent_containers_safely",
            AsyncMock(side_effect=AssertionError("PATCH 不得触发改名（删容器）")),
        )

    @pytest.mark.asyncio
    async def test_omitted_fields_keep_current_values(self, monkeypatch):
        """只给 model ⇒ 其余字段（driver / allow_tools / allow_skills / i18n / name）全保持。"""
        agent = _agent(11, "alice", model="old-model")
        agent.allow_tools = ["Category:Read"]
        agent.allow_skills = ["frontend-design"]
        agent.i18n = {"display_name": {"zh-CN": "爱丽丝"}}
        self._patch(monkeypatch, agent)

        result = await tools.save_agent(agent_id=11, model="new-model", _context=_ctx())

        assert result["success"] is True, result
        assert agent.model == "new-model"
        assert agent.name == "alice", "省略 name 不得被当成改名"
        assert agent.driver == DriverType.NATIVE, "省略 driver 必须保持现值（不是回落 tsp）"
        assert agent.allow_tools == ["Category:Read"]
        assert agent.allow_skills == ["frontend-design"]
        assert agent.i18n == {"display_name": {"zh-CN": "爱丽丝"}}

    @pytest.mark.asyncio
    async def test_clear_fields_restore_defaults(self, monkeypatch):
        """`clear_fields` ⇒ model 落 `""`、allow_tools / allow_skills 落 `None`。"""
        agent = _agent(11, "alice", model="old-model")
        agent.allow_tools = ["Category:Read"]
        agent.allow_skills = ["frontend-design"]
        self._patch(monkeypatch, agent)

        result = await tools.save_agent(
            agent_id=11,
            clear_fields=["model", "allow_tools", "allow_skills"],
            _context=_ctx(),
        )

        assert result["success"] is True, result
        assert agent.model == ""
        assert agent.allow_tools is None
        assert agent.allow_skills is None

    @pytest.mark.asyncio
    async def test_value_plus_clear_is_ambiguous_and_writes_nothing(self, monkeypatch):
        """同字段既给值又 clear ⇒ `ambiguous_clear_field`，且一次写库都没发生。"""
        agent = _agent(11, "alice", model="old-model")
        self._patch(monkeypatch, agent)

        result = await tools.save_agent(
            agent_id=11, model="new-model", clear_fields=["model"], _context=_ctx()
        )

        assert result["success"] is False
        assert result["error_code"] == "ambiguous_clear_field"
        assert agent.model == "old-model"
        tools.gtAgentManager.batch_save_agents.assert_not_called()

    @pytest.mark.asyncio
    async def test_unknown_clear_field_is_rejected(self, monkeypatch):
        """白名单之外 ⇒ `invalid_clear_field`，并在文案里给出可用项。"""
        agent = _agent(11, "alice")
        self._patch(monkeypatch, agent)

        result = await tools.save_agent(agent_id=11, clear_fields=["driver"], _context=_ctx())

        assert result["success"] is False
        assert result["error_code"] == "invalid_clear_field"
        assert "driver" in result["message"]
        assert "model" in result["message"]
        tools.gtAgentManager.batch_save_agents.assert_not_called()

    @pytest.mark.asyncio
    async def test_bad_clear_field_type_is_rejected(self, monkeypatch):
        """非 str / 非 list[str]（这里传 int）⇒ `invalid_clear_field`，不是未捕获 TypeError。"""
        agent = _agent(11, "alice")
        self._patch(monkeypatch, agent)

        result = await tools.save_agent(agent_id=11, clear_fields=5, _context=_ctx())

        assert result["success"] is False
        assert result["error_code"] == "invalid_clear_field"
        assert "int" in result["message"]

    @pytest.mark.asyncio
    async def test_create_still_requires_role_template_at_runtime(self, monkeypatch):
        """新建时 `role_template_name` 仍必填 —— 只是校验收到了运行时（schema 已放开）。"""
        monkeypatch.setattr(tools.gtAgentManager, "get_agent", AsyncMock(return_value=None))
        monkeypatch.setattr(tools.gtAgentManager, "get_agent_by_id", AsyncMock(return_value=None))
        monkeypatch.setattr(tools.gtAgentManager, "batch_save_agents", AsyncMock())

        result = await tools.save_agent(name="newbie", _context=_ctx())

        assert result["success"] is False
        assert "role_template_name" in result["message"]
        tools.gtAgentManager.batch_save_agents.assert_not_called()


class TestP1bSaveDeptPatch:
    """`save_dept` 的省略 / 给值 / clear，重点钉 `parent_name`。"""

    def _patch(self, monkeypatch, dept: GtDept, *, agents: list[GtAgent]) -> None:
        monkeypatch.setattr(deptService.gtDeptManager, "get_dept_by_id", AsyncMock(return_value=dept))
        monkeypatch.setattr(deptService.gtDeptManager, "get_dept_by_name", AsyncMock(return_value=dept))
        monkeypatch.setattr(tools.gtAgentManager, "get_team_all_agents", AsyncMock(return_value=agents))
        monkeypatch.setattr(deptService, "upsert_dept", AsyncMock(return_value=dept))
        monkeypatch.setattr(deptService, "get_dept_tree", AsyncMock(return_value=None))

    @pytest.mark.asyncio
    async def test_omitted_parent_keeps_existing_parent(self, monkeypatch):
        """省略 `parent_name` ⇒ 保持现有父组织（改造前这里会报错或落成根）。"""
        parent = _dept(1, "root", manager_id=1)
        child = _dept(2, "child", parent_id=1, manager_id=2)
        self._patch(monkeypatch, child, agents=[_agent(1, "alice"), _agent(2, "bob")])

        result = await tools.save_dept(dept_id=2, responsibility="新职责", _context=_ctx())

        assert result["success"] is True, result
        kwargs = deptService.upsert_dept.await_args.kwargs
        assert kwargs["parent_id"] == 1, "省略 parent_name 必须保持 parent_id=1"
        assert kwargs["responsibility"] == "新职责"
        assert kwargs["manager_id"] == 2, "省略 manager_name 必须保持现负责人"
        assert sorted(kwargs["agent_ids"]) == [2], "省略 member_names 必须保持现成员"

    @pytest.mark.asyncio
    async def test_clear_parent_only_allowed_for_root(self, monkeypatch):
        """`clear_fields=["parent_name"]` 只在「已经是根」时允许；否则报错不改结构。"""
        root = _dept(1, "root", manager_id=1)
        child = _dept(2, "child", parent_id=1, manager_id=2)
        agents = [_agent(1, "alice"), _agent(2, "bob")]

        self._patch(monkeypatch, root, agents=agents)
        on_root = await tools.save_dept(dept_id=1, clear_fields=["parent_name"], _context=_ctx())

        self._patch(monkeypatch, child, agents=agents)
        deptService.upsert_dept.reset_mock()
        on_child = await tools.save_dept(dept_id=2, clear_fields=["parent_name"], _context=_ctx())

        assert on_root["success"] is True, on_root
        assert on_child["success"] is False
        assert "根组织" in on_child["message"]
        deptService.upsert_dept.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_requires_all_fields_at_runtime(self, monkeypatch):
        """新建缺字段 ⇒ 文案**一次列全**缺失项（不是缺一个报一个）。"""
        monkeypatch.setattr(deptService.gtDeptManager, "get_dept_by_name", AsyncMock(return_value=None))
        monkeypatch.setattr(tools.gtAgentManager, "get_team_all_agents", AsyncMock(return_value=[]))
        monkeypatch.setattr(deptService, "upsert_dept", AsyncMock())

        result = await tools.save_dept(name="newdept", _context=_ctx())

        assert result["success"] is False
        for field in ("responsibility", "manager_name", "member_names"):
            assert field in result["message"], (field, result["message"])
        deptService.upsert_dept.assert_not_called()


class TestP1bSaveRoomPatch:
    """`save_room` 的省略 / 给值 / clear，重点钉 `initial_topic` 与 `max_rounds`。"""

    def _patch(self, monkeypatch, room: SimpleNamespace, *, agents: list[GtAgent]) -> None:
        monkeypatch.setattr(tools.gtRoomManager, "get_room_by_id", AsyncMock(return_value=room))
        monkeypatch.setattr(tools.gtRoomManager, "get_room_by_team_and_name", AsyncMock(return_value=room))
        monkeypatch.setattr(tools.gtAgentManager, "get_team_all_agents", AsyncMock(return_value=agents))
        monkeypatch.setattr(roomService, "upsert_room", AsyncMock(return_value=room))

    def _room(self) -> SimpleNamespace:
        return SimpleNamespace(
            id=5, team_id=TEAM_ID, name="room_a", type=RoomType.PRIVATE,
            agent_ids=[1, 2], tags=[], initial_topic="旧话题", max_rounds=7, i18n={},
        )

    @pytest.mark.asyncio
    async def test_omitted_topic_keeps_current_value(self, monkeypatch):
        """省略 `initial_topic` ⇒ 保持现值（改造前默认 `""` 会把话题清空）。"""
        room = self._room()
        self._patch(monkeypatch, room, agents=[_agent(1, "alice"), _agent(2, "bob")])

        result = await tools.save_room(room_id=5, max_rounds=3, _context=_ctx())

        assert result["success"] is True, result
        kwargs = roomService.upsert_room.await_args.kwargs
        assert kwargs["initial_topic"] == "旧话题", "省略 initial_topic 必须保持现值"
        assert kwargs["max_rounds"] == 3
        assert kwargs["agent_ids"] == [1, 2], "省略 member_names 必须保持现成员"

    @pytest.mark.asyncio
    async def test_clear_topic_lands_empty_string(self, monkeypatch):
        """`clear_fields=["initial_topic"]` ⇒ 落 `""`（不是 None：列是 CharField）。"""
        room = self._room()
        self._patch(monkeypatch, room, agents=[_agent(1, "alice"), _agent(2, "bob")])

        result = await tools.save_room(room_id=5, clear_fields=["initial_topic"], _context=_ctx())

        assert result["success"] is True, result
        assert roomService.upsert_room.await_args.kwargs["initial_topic"] == ""

    @pytest.mark.asyncio
    async def test_clear_max_rounds_lands_none(self, monkeypatch):
        """`clear_fields=["max_rounds"]` ⇒ 落 `None`（= 回到系统默认）。"""
        room = self._room()
        self._patch(monkeypatch, room, agents=[_agent(1, "alice"), _agent(2, "bob")])

        result = await tools.save_room(room_id=5, clear_fields=["max_rounds"], _context=_ctx())

        assert result["success"] is True, result
        assert roomService.upsert_room.await_args.kwargs["max_rounds"] is None

    @pytest.mark.asyncio
    async def test_create_requires_member_names_at_runtime(self, monkeypatch):
        monkeypatch.setattr(tools.gtRoomManager, "get_room_by_team_and_name", AsyncMock(return_value=None))
        monkeypatch.setattr(tools.gtAgentManager, "get_team_all_agents", AsyncMock(return_value=[]))
        monkeypatch.setattr(roomService, "upsert_room", AsyncMock())

        result = await tools.save_room(name="newroom", _context=_ctx())

        assert result["success"] is False
        assert "member_names" in result["message"]
        roomService.upsert_room.assert_not_called()


# =========================================================================== #
# P1c：dry_run
# =========================================================================== #


class TestP1cDeptDryRun:
    """`deptService.delete_dept(dry_run=True)`：校验全跑、写入零调用。"""

    def _patch(self, monkeypatch, depts: list[GtDept], *, children: bool) -> None:
        monkeypatch.setattr(deptService.gtDeptManager, "get_all_depts", AsyncMock(return_value=depts))
        monkeypatch.setattr(deptService.gtDeptManager, "delete_depts_by_ids", AsyncMock())
        monkeypatch.setattr(deptService, "get_dept_tree", AsyncMock(return_value=None))
        monkeypatch.setattr(deptService.roomService, "overwrite_dept_rooms", AsyncMock())

    @pytest.mark.asyncio
    async def test_dry_run_returns_doomed_without_writing(self, monkeypatch):
        """返回完整清单（自身 → 子 → 孙，树序），且**一次写入都没发生**。"""
        root = _dept(1, "root", manager_id=1)
        child = _dept(2, "child", parent_id=1, manager_id=2)
        grandchild = _dept(3, "grandchild", parent_id=2, manager_id=3)
        self._patch(monkeypatch, [root, child, grandchild], children=True)

        doomed = await deptService.delete_dept(TEAM_ID, 2, recursive=True, dry_run=True)

        assert [d.id for d in doomed] == [2, 3]
        deptService.gtDeptManager.delete_depts_by_ids.assert_not_called()
        deptService.roomService.overwrite_dept_rooms.assert_not_called()

    @pytest.mark.asyncio
    async def test_dry_run_still_blocks_children_without_recursive(self, monkeypatch):
        """预演必须能暴露「需要 recursive」——校验不因为是 dry run 就跳过。"""
        root = _dept(1, "root", manager_id=1)
        child = _dept(2, "child", parent_id=1, manager_id=2)
        grandchild = _dept(3, "grandchild", parent_id=2, manager_id=3)
        self._patch(monkeypatch, [root, child, grandchild], children=True)

        # 目标必须是「非根 + 真有子」：删根会先被根保护拦下，删叶子则没有子部门可说。
        with pytest.raises(Exception) as exc_info:
            await deptService.delete_dept(TEAM_ID, 2, recursive=False, dry_run=True)

        assert "子部门" in str(exc_info.value)
        deptService.gtDeptManager.delete_depts_by_ids.assert_not_called()

    @pytest.mark.asyncio
    async def test_root_still_protected_in_dry_run(self, monkeypatch):
        root = _dept(1, "root", manager_id=1)
        self._patch(monkeypatch, [root], children=False)

        with pytest.raises(Exception) as exc_info:
            await deptService.delete_dept(TEAM_ID, 1, dry_run=True)

        assert "根部门" in str(exc_info.value)
        deptService.gtDeptManager.delete_depts_by_ids.assert_not_called()

    @pytest.mark.asyncio
    async def test_tool_layer_reports_would_delete_vs_deleted(self, monkeypatch):
        """工具层：dry run 回 `would_delete`，真删回 `deleted`（字段名区分，不能用同一个）。"""
        root = _dept(1, "root", manager_id=1)
        child = _dept(2, "child", parent_id=1, manager_id=2)
        self._patch(monkeypatch, [root, child], children=False)
        monkeypatch.setattr(
            deptService.gtDeptManager, "get_dept_by_name", AsyncMock(return_value=child)
        )

        preview = await tools.delete_dept(name="child", dry_run=True, _context=_ctx())
        assert preview["success"] is True
        assert preview["dry_run"] is True
        assert preview["would_delete"] == [{"id": 2, "name": "child"}]
        assert "deleted" not in preview
        assert "dry run" in preview["message"]

        monkeypatch.setattr(deptService, "delete_dept", AsyncMock(return_value=[child]))
        monkeypatch.setattr(deptService.gtDeptManager, "get_dept_by_name", AsyncMock(return_value=child))
        real = await tools.delete_dept(name="child", _context=_ctx())
        assert real["dry_run"] is False
        assert real["deleted"] == [{"id": 2, "name": "child"}]
        assert "would_delete" not in real


class TestP1cRoomDryRun:
    """`roomService.delete_managed_room(dry_run=True)`。"""

    def _patch(self, monkeypatch, room: SimpleNamespace, *, state=None) -> None:
        monkeypatch.setattr(tools.gtRoomManager, "get_room_by_id", AsyncMock(return_value=room))
        monkeypatch.setattr(tools.gtRoomManager, "get_room_by_team_and_name", AsyncMock(return_value=room))
        monkeypatch.setattr(roomCore.gtRoomManager, "get_room_by_id", AsyncMock(return_value=room))
        monkeypatch.setattr(roomCore.gtRoomManager, "delete_room", AsyncMock())
        # ⚠️ `delete_managed_room` 在 `roomService.core` 里，取的是**该模块**的全局
        # `get_room`；patch 包层 `roomService.get_room` 改不到它（会静默不生效）。
        if state is None:
            monkeypatch.setattr(roomCore, "get_room", lambda room_id: None)
        else:
            monkeypatch.setattr(roomCore, "get_room", lambda room_id: SimpleNamespace(state=state))

    def _room(self, *, tags: list[str] | None = None) -> SimpleNamespace:
        return SimpleNamespace(
            id=5, team_id=TEAM_ID, name="room_a", type=RoomType.PRIVATE,
            agent_ids=[1, 2], tags=tags or [], initial_topic="", max_rounds=None, i18n={},
        )

    @pytest.mark.asyncio
    async def test_dry_run_skips_delete(self, monkeypatch):
        self._patch(monkeypatch, self._room())

        await roomService.delete_managed_room(TEAM_ID, 5, dry_run=True)

        roomCore.gtRoomManager.delete_room.assert_not_called()

    @pytest.mark.asyncio
    async def test_dry_run_still_enforces_dept_protection(self, monkeypatch):
        """DEPT 房间保护在 dry run 下同样生效（预演必须能给出「删不了」）。"""
        self._patch(monkeypatch, self._room(tags=["DEPT"]))

        with pytest.raises(Exception) as exc_info:
            await roomService.delete_managed_room(TEAM_ID, 5, dry_run=True)

        assert "DEPT" in str(exc_info.value)
        roomCore.gtRoomManager.delete_room.assert_not_called()

    @pytest.mark.asyncio
    async def test_dry_run_still_enforces_active_protection(self, monkeypatch):
        from constants import RoomState

        self._patch(monkeypatch, self._room(), state=RoomState.SCHEDULING)

        with pytest.raises(Exception) as exc_info:
            await roomService.delete_managed_room(TEAM_ID, 5, dry_run=True)

        assert "运行中" in str(exc_info.value)
        roomCore.gtRoomManager.delete_room.assert_not_called()

    @pytest.mark.asyncio
    async def test_tool_layer_reports_would_delete_vs_deleted(self, monkeypatch):
        room = self._room()
        self._patch(monkeypatch, room)

        preview = await tools.delete_room(room_id=5, dry_run=True, _context=_ctx())
        assert preview["dry_run"] is True
        assert preview["would_delete"] == [{"id": 5, "name": "room_a"}]

        real = await tools.delete_room(room_id=5, _context=_ctx())
        assert real["dry_run"] is False
        assert real["deleted"] == [{"id": 5, "name": "room_a"}]
        assert roomCore.gtRoomManager.delete_room.await_count == 1, "只有真删那次调用了写入"

    @pytest.mark.asyncio
    async def test_requires_at_least_one_locator(self, monkeypatch):
        """两个定位都不给 ⇒ 明确报错（不是「未找到 name=None」）。"""
        result = await tools.delete_room(_context=_ctx())

        assert result["success"] is False
        assert "room_id" in result["message"] and "name" in result["message"]


# =========================================================================== #
# P1d：required 放开后的「运行时校验等效于 schema 校验」（小马哥 11:40 硬条件 1/2）
#
# 背景：PATCH 语义要求「未传」可达，而 schema 层 `required` 会在进函数**之前**就把
# 「未传」拦掉 ⇒ 只能把 save 系列的必填判据下移到函数体内。下移不是放松：
# 三条新建路径各自仍必须被拒、文案必须点名参数、且**一次写库都不许发生**。
# 每一条都配了变异自证（删掉运行时校验 ⇒ 用例必须变红），见报告 §「变异自证」。
# =========================================================================== #


class TestP1dRequiredMovedToRuntime:
    """schema 的 `required` 变空之后，新建缺参数仍必须被拒（不是静默建出无名对象）。"""

    @pytest.mark.asyncio
    async def test_save_agent_without_name_or_id_is_rejected(self, monkeypatch):
        """既没 agent_id 也没 name ⇒ 拒绝并**点名这两个参数**，不写库。"""
        monkeypatch.setattr(tools.gtAgentManager, "get_agent", AsyncMock(return_value=None))
        monkeypatch.setattr(tools.gtAgentManager, "get_agent_by_id", AsyncMock(return_value=None))
        monkeypatch.setattr(tools.gtAgentManager, "batch_save_agents", AsyncMock())

        result = await tools.save_agent(model="gpt-4o-mini", _context=_ctx())

        assert result["success"] is False
        assert "agent_id" in result["message"] and "name" in result["message"]
        tools.gtAgentManager.batch_save_agents.assert_not_called()

    @pytest.mark.asyncio
    async def test_save_dept_without_name_or_id_is_rejected(self, monkeypatch):
        monkeypatch.setattr(deptService.gtDeptManager, "get_dept_by_name", AsyncMock(return_value=None))
        monkeypatch.setattr(deptService, "upsert_dept", AsyncMock())

        result = await tools.save_dept(responsibility="x", _context=_ctx())

        assert result["success"] is False
        assert "dept_id" in result["message"] and "name" in result["message"]
        deptService.upsert_dept.assert_not_called()

    @pytest.mark.asyncio
    async def test_save_room_without_name_or_id_is_rejected(self, monkeypatch):
        monkeypatch.setattr(tools.gtRoomManager, "get_room_by_team_and_name", AsyncMock(return_value=None))
        monkeypatch.setattr(roomService, "upsert_room", AsyncMock())

        result = await tools.save_room(max_rounds=3, _context=_ctx())

        assert result["success"] is False
        assert "room_id" in result["message"] and "name" in result["message"]
        roomService.upsert_room.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_naked_db_constraint_error_ever_surfaces(self, monkeypatch):
        """`NOT NULL constraint failed` 绝不能浮到用户面前（硬条件 1 的原话）。

        这条不只测 save_agent 一条路径：把「缺参数」的各种组合扫一遍，任何一个返回体
        里出现裸数据库措辞即红 —— 那说明校验真的漏了、写库被尝试过。
        """
        monkeypatch.setattr(tools.gtAgentManager, "get_agent", AsyncMock(return_value=None))
        monkeypatch.setattr(tools.gtAgentManager, "get_agent_by_id", AsyncMock(return_value=None))
        monkeypatch.setattr(tools.gtAgentManager, "batch_save_agents", AsyncMock())
        monkeypatch.setattr(deptService.gtDeptManager, "get_dept_by_name", AsyncMock(return_value=None))
        monkeypatch.setattr(deptService, "upsert_dept", AsyncMock())
        monkeypatch.setattr(tools.gtRoomManager, "get_room_by_team_and_name", AsyncMock(return_value=None))
        monkeypatch.setattr(roomService, "upsert_room", AsyncMock())
        # `save_room` 在定位之后会先读一次名册（PATCH 语义反查名字），不 patch 会真去连库。
        monkeypatch.setattr(tools.gtAgentManager, "get_team_all_agents", AsyncMock(return_value=[]))

        calls = [
            tools.save_agent(_context=_ctx()),
            tools.save_agent(name="newbie", _context=_ctx()),          # 缺 role_template_name
            tools.save_dept(_context=_ctx()),
            tools.save_dept(name="d", _context=_ctx()),                # 缺 4 项
            tools.save_room(_context=_ctx()),
            tools.save_room(name="r", _context=_ctx()),                # 缺 member_names
        ]
        for result in await asyncio.gather(*calls):
            assert result["success"] is False, result
            assert "constraint failed" not in result["message"].lower(), result
            assert "IntegrityError" not in result["message"], result

        tools.gtAgentManager.batch_save_agents.assert_not_called()
        deptService.upsert_dept.assert_not_called()
        roomService.upsert_room.assert_not_called()


# =========================================================================== #
# P1e：运行时校验的「错误码等效于 schema 层」（小马哥 12:02 / 12:16 阻塞项）
#
# 为什么单列一组：`error_code` 是**程序分支键**，不是措辞。文案点名只解决「人读得懂」，
# 若码是 `None`，「参数缺失（补参数重试）」与「业务失败（换操作）」在机器层面不可辨。
# 上一版这里 5 条路径全是 `None` —— 小马哥判为违反硬条件 1 的「等效」要求。
#
# ⚠️ 判据是**正向断言两条**：① 必须有 `error_code == "invalid_tool_args"`；
# ② 必须与业务失败（「已存在需 overwrite」）的码取值**不同**。
# 反例见 `test_no_naked_db_constraint_error_ever_surfaces`：它只断言「不得出现某串」
# ——否定式断言在功能未发生时恒真，删掉运行时校验它照样 passed，故不计入本组覆盖。
# =========================================================================== #


class TestP1eRuntimeLocatorErrorCode:
    """五个「缺定位参数」入口都必须给出与 schema 层同码的 `invalid_tool_args`。"""

    def _patch_empty(self, monkeypatch) -> None:
        monkeypatch.setattr(tools.gtAgentManager, "get_agent", AsyncMock(return_value=None))
        monkeypatch.setattr(tools.gtAgentManager, "get_team_all_agents", AsyncMock(return_value=[]))
        monkeypatch.setattr(tools.gtAgentManager, "get_agent_by_id", AsyncMock(return_value=None))
        monkeypatch.setattr(tools.gtAgentManager, "batch_save_agents", AsyncMock())
        monkeypatch.setattr(deptService.gtDeptManager, "get_dept_by_name", AsyncMock(return_value=None))
        monkeypatch.setattr(deptService, "upsert_dept", AsyncMock())
        monkeypatch.setattr(tools.gtRoomManager, "get_room_by_team_and_name", AsyncMock(return_value=None))
        monkeypatch.setattr(roomService, "upsert_room", AsyncMock())

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "invoke",
        [
            pytest.param(lambda: tools.save_agent(_context=_ctx()), id="save_agent"),
            pytest.param(lambda: tools.save_dept(_context=_ctx()), id="save_dept"),
            pytest.param(lambda: tools.save_room(_context=_ctx()), id="save_room"),
            pytest.param(lambda: tools.delete_dept(_context=_ctx()), id="delete_dept"),
            pytest.param(lambda: tools.delete_room(_context=_ctx()), id="delete_room"),
        ],
    )
    async def test_missing_locator_code_equals_schema_layer(self, monkeypatch, invoke):
        """缺定位参数 ⇒ `error_code == "invalid_tool_args"`（与 schema 层同码），文案点名参数。"""
        self._patch_empty(monkeypatch)

        result = await invoke()

        assert result["success"] is False, result
        assert result["error_code"] == "invalid_tool_args", result
        # 同码之外仍点名参数：机器可分支、人可照着补。
        assert "name" in result["message"], result

    @pytest.mark.asyncio
    async def test_missing_locator_code_is_distinguishable_from_business_failure(self, monkeypatch):
        """「参数缺失」与「同名已存在需 overwrite」的码**取值不同**，调用方能选对下一步。

        两条路径都在工具层、都 `success=False`；若都返回 `None`（上一版的状态），
        调用方无法凭返回体判断「补参数重试」还是改传 `overwrite_existing=true`。
        """
        self._patch_empty(monkeypatch)
        existing = _agent(11, "alice", model="old-model")
        monkeypatch.setattr(tools.gtAgentManager, "get_agent", AsyncMock(return_value=existing))
        monkeypatch.setattr(
            tools.gtAgentManager, "get_team_all_agents", AsyncMock(return_value=[existing])
        )

        missing = await tools.save_agent(_context=_ctx())
        business = await tools.save_agent(name="alice", role_template_name="writer", _context=_ctx())

        assert missing["success"] is False and business["success"] is False
        assert missing["error_code"] == "invalid_tool_args", missing
        # 取值写出来（不是「不相同」三个字）：业务失败这一档当前**不带** error_code。
        assert "error_code" not in business, business
        assert business.get("error_code") is None, business
        assert "overwrite_existing" in business["message"], business


class TestP1ePatchEchoName:
    """按 id 做 PATCH、省略 name 时，返回体不得出现 `None` 名字（脏文案经 console 直达模型）。"""

    @pytest.mark.asyncio
    async def test_patch_by_id_echoes_existing_name(self, monkeypatch):
        """`save_agent(agent_id=11, model="x")` ⇒ 无 `"None"` 字样，`agent.name` 回显非空。"""
        agent = _agent(11, "alice", model="old-model")
        monkeypatch.setattr(tools.gtAgentManager, "get_agent_by_id", AsyncMock(return_value=agent))
        monkeypatch.setattr(tools.gtAgentManager, "batch_save_agents", AsyncMock())
        monkeypatch.setattr(
            tools.gtRoleTemplateManager, "get_role_template_by_id",
            AsyncMock(return_value=SimpleNamespace(id=21, name="writer")),
        )
        monkeypatch.setattr(
            tools.containerService, "delete_agent_containers_safely",
            AsyncMock(side_effect=AssertionError("PATCH 不得触发改名（删容器）")),
        )

        result = await tools.save_agent(agent_id=11, model="new-model", _context=_ctx())

        assert result["success"] is True, result
        assert "None" not in result["message"], result["message"]
        assert result["agent"]["name"] == "alice", result["agent"]
        assert "alice" in result["message"], result["message"]
