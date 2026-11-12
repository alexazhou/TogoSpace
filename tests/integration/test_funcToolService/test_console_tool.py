"""`console` 工具（#154）集成测试：CC1–CC6。

判据出处：任务单 #154 与设计稿 `draft_v_console.md`。本文件只测「行为」，不测实现细节
——所有断言都打在**可观察结果**上（返回体关键字段、DB 里的真实状态、错误码），
这样重构内部结构不会让用例变红，而真正的行为漂移会。

CC7（全量回归）不在这里，它是跑 `pytest tests/unit` 并与基线对比，见报告。

一条口径说明（为什么 CC1 要逐字段比而不是只比 `success`）：
任务单明确写「不是'能跑通'就算过」。`console` 的价值在于**等价**——如果它把
`save_dept` 的 `member_names` 丢了却仍返回 `success: True`，光看 success 是完全
发现不了的。所以每条命令都比「底层原工具返回体的关键字段」。
"""

from __future__ import annotations

import json
import os
import sys

import pytest

import service.agentService as agentService
import service.deptService as deptService
import service.ormService as ormService
import service.persistenceService as persistenceService
import service.roomService as roomService
from constants import AgentStatus, EmployStatus, RoleTemplateType
from exception import ForwardToolCall
from dal.db import gtAgentManager, gtDeptManager, gtRoleTemplateManager, gtRoomManager, gtTeamManager
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtDept import GtDept
from model.dbModel.gtRoleTemplate import GtRoleTemplate
from model.dbModel.gtTeam import GtTeam
from service import funcToolService
from service.agentService.toolRegistry import AgentToolRegistry, build_runtime_allow_specs
from service.funcToolService import consoleCommands as cc
from service.funcToolService import consoleParsing as cp
from service.funcToolService import core as func_tool_core
from service.funcToolService.consoleTool import console_triggers_self_interrupt
from service.funcToolService.tools import (
    delete_dept,
    delete_role_template,
    delete_room,
    get_agent_info,
    get_dept_info,
    get_role_template,
    get_room_info,
    get_time,
    list_role_templates,
    save_agent,
    save_dept,
    save_role_template,
    save_room,
    wake_up_agent,
)
from service.roomService import ToolCallContext
from util import jsonUtil
from util.llmApiUtil import OpenAIToolCall

from ...base import ServiceTestCase

TEAM = "test_team"

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


class _FakeRuntimeAgent:
    """`agentService.get_team_agents` 的最小替身（只给工具用到的两个字段）。"""

    def __init__(self, gt_agent: GtAgent, status: AgentStatus) -> None:
        self.gt_agent = gt_agent
        self.status = status
        self.resumed = False

    def start_consumer_task(self) -> None:
        self.resumed = True


def _tool_call(command: str, payload: dict | None = None) -> OpenAIToolCall:
    """构造一个 OpenAIToolCall，用于测动态自中断判定器。"""
    args: dict = {"command": command}
    if payload is not None:
        args["payload"] = payload
    return OpenAIToolCall(
        id="call_1",
        function={"name": "console", "arguments": json.dumps(args, ensure_ascii=False)},
    )


def _as_tool_result(payload: dict) -> dict:
    """把「直接调原工具」的返回体过一遍与执行入口**相同**的 JSON 归一化。

    为什么要这一步：`AgentToolRegistry.execute_tool_call` 对所有工具结果都做
    `jsonUtil.object_to_json_data`（datetime → 格式化字符串、Enum → name……）。
    拿原工具的**原始**返回值去比 `via`，比的是「未归一化 vs 已归一化」，
    差异全部来自序列化层，和「console 是否等价于原工具」无关。
    所以两侧都过同一个函数——保留等价性判据的锋利度，又不引入假红。
    """
    return jsonUtil.object_to_json_data(payload)


def _build_registry(*, is_root_leader: bool) -> AgentToolRegistry:
    """构造一个与生产 driver **同形**的注册表（#160）。

    为什么必须这样测：改造后 `console` 只负责抛 `ForwardToolCall`，鉴权与执行都在
    `AgentToolRegistry.execute_tool_call`。直接 `await console(...)` 测的是一条生产上
    不存在的链路（转发信号会一路冒到用例里），权限判据也就变成「console 自己的实现」
    ——正是本单要消灭的那份重复实现。

    注册口径照抄三个 driver：`marks_turn_finish` / 静态 `self_interrupt`（`reload_team`）
    / 动态判定器（`console`）三者缺一，D6 的自中断行为就测不到。

    用 `load_func_tools()` 而不是 `get_tools()`：后者受 D2 开关影响会隐藏 16 个原工具，
    但**执行侧必须始终可用**（console 的第 2 跳要查得到它们），这正是 D2 的约定。
    """
    registry = AgentToolRegistry()
    for name, func_tool in func_tool_core.load_func_tools().items():
        registry.register(
            func_tool.to_openai_tool(),
            funcToolService.run_tool_call,
            marks_turn_finish=name == "finish_action",
            self_interrupt=name == "reload_team",
            self_interrupt_predicate=func_tool_core.get_self_interrupt_predicate(name),
        )
    registry.apply_tool_allow_specs(
        build_runtime_allow_specs(None, is_root_leader=is_root_leader)
    )
    return registry


_REGISTRIES: dict[bool, AgentToolRegistry] = {}
"""全局注册表缓存，键是 `is_root_leader`（#160）。

注册表的内容只由 `is_root_leader` 决定（`build_runtime_allow_specs` 只看这一个布尔，
`load_func_tools()` 与 DB 无关），所以整个模块只需要两份。按 `agent_id` 缓存会把同一
份东西复制成「身份数」份，还会让每个测试类各建一套——那是在缓存键上写错了维度。
"""


class _ConsoleViaRegistry:
    """让用例统一经「唯一执行入口」调 `console`（#160 的核心约束）。

    注册表在 `async_setup_class` 里**一次性预建**（`_prepare_registries`），不在用例里
    现造：既省掉 92 次重复的 schema 构建，也让夹具坏掉时**类级 setup 直接失败**，而不是
    等到某个用例以「工具无权限使用」的形式假红。

    子类只需调一次 `_prepare_registries(<根部门负责人 id>)` 并实现 `_ctx()`。
    """

    _root_leader_id: int

    @classmethod
    def _prepare_registries(cls, root_leader_id: int) -> None:
        """记下「谁是根主管」，并把两种权限身份各一份注册表建好（类级 setup 里调）。"""
        cls._root_leader_id = root_leader_id
        for is_root_leader in (True, False):
            _REGISTRIES.setdefault(
                is_root_leader, _build_registry(is_root_leader=is_root_leader)
            )

    def _registry_for(self, agent_id: int) -> AgentToolRegistry:
        return _REGISTRIES[agent_id == self._root_leader_id]

    async def _call(self, command: str, payload: dict | None = None, _context=None) -> dict:
        """发一次 console 工具调用，返回「执行入口」给出的结果体。

        签名刻意保留 `_context`：用例原本就用它表达身份，改成 `who=` 只会让 diff 变大、
        却不多测任何东西。
        """
        context = _context if _context is not None else self._ctx()
        result = await self._registry_for(context.agent_id).execute_tool_call(
            _tool_call(command, payload), context
        )
        return result.result


class TestConsoleTool(_ConsoleViaRegistry, ServiceTestCase):
    """`console` 主用例集：路由等价 / 权限 / 输入来源 / 删除保护 / PATCH。"""

    @classmethod
    async def async_setup_class(cls):
        db_path = cls._get_test_db_path()
        await ormService.startup(db_path)
        await persistenceService.startup()
        await agentService.startup()
        await roomService.startup()

        team = await gtTeamManager.save_team(GtTeam(name=TEAM))
        template = await gtRoleTemplateManager.save_role_template(
            GtRoleTemplate(name="test_role", soul="test tool role")
        )
        # 为什么需要 mgr1..mgr9：`deptService.upsert_dept` 有两条业务规则——
        # ① 一个成员只能担任一个部门的负责人；② 一个成员只能属于一个部门（负责人可
        # 保留在父部门）。用例里每个部门都用**独占的一对成员**，否则后建部门会把
        # 前面部门的成员「吸走」，导致前面用例的断言随执行顺序漂移。这是夹具问题，
        # 不是产品缺陷。
        await gtAgentManager.batch_save_agents(
            team.id,
            [
                GtAgent(team_id=team.id, name="alice", role_template_id=template.id),
                GtAgent(team_id=team.id, name="bob", role_template_id=template.id),
                GtAgent(team_id=team.id, name="charlie", role_template_id=template.id),
                GtAgent(team_id=team.id, name="mgr1", role_template_id=template.id),
                GtAgent(team_id=team.id, name="mgr2", role_template_id=template.id),
                GtAgent(team_id=team.id, name="mgr3", role_template_id=template.id),
                GtAgent(team_id=team.id, name="mgr4", role_template_id=template.id),
                GtAgent(team_id=team.id, name="mgr5", role_template_id=template.id),
                GtAgent(team_id=team.id, name="mgr6", role_template_id=template.id),
                GtAgent(team_id=team.id, name="mgr7", role_template_id=template.id),
                GtAgent(team_id=team.id, name="mgr8", role_template_id=template.id),
                GtAgent(team_id=team.id, name="mgr9", role_template_id=template.id),
            ],
        )
        agents = await gtAgentManager.get_team_all_agents(team.id)
        cls.agent_ids = {a.name: a.id for a in agents}
        cls.team_id = team.id
        cls.role_template_id = template.id

        alice = next(a for a in agents if a.name == "alice")
        bob = next(a for a in agents if a.name == "bob")
        charlie = next(a for a in agents if a.name == "charlie")

        # alice 是根部门负责人 ⇒ 运行时得到 Category:Admin（`build_runtime_allow_specs`
        # 只在 is_root_leader 时补 Admin）。bob / charlie 不是根负责人、allow_tools=None
        # ⇒ 只有 Basic/Read/Write/Execute，**没有** Admin。D3 的权限用例就靠这个差别。
        await gtTeamManager.set_team_enabled(team.id, False)
        await deptService.overwrite_dept_tree(
            team.id,
            GtDept(
                name="root",
                responsibility="root dept",
                manager_id=alice.id,
                agent_ids=[alice.id, bob.id, charlie.id],
            ),
        )
        await gtTeamManager.set_team_enabled(team.id, True)

        # 注册表在类级 setup 里一次性预建（整个模块共用两份）。
        # 身份差异只有 is_root_leader（alice 是根部门负责人 ⇒ 多一个 Category:Admin）。
        cls._prepare_registries(alice.id)
        cls.cfg = {"db_path": db_path}

    @classmethod
    async def async_teardown_class(cls):
        await agentService.shutdown()
        roomService.shutdown()
        await persistenceService.shutdown()
        await ormService.shutdown()

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    def _ctx(self, name: str = "alice") -> ToolCallContext:
        """默认以 alice（根主管，有 Admin）身份调用，权限用例会显式换成 bob。"""
        return ToolCallContext(agent_id=self.agent_ids[name], team_id=self.team_id, chat_room=None)

    async def _fake_runtime_agents(self, statuses: dict[str, AgentStatus] | None = None):
        """构造运行态 agent 桩列表（异步取 DB，避免在 async 用例里跑事件循环）。"""
        rows = await gtAgentManager.get_team_all_agents(self.team_id)
        statuses = statuses or {}
        return [_FakeRuntimeAgent(row, statuses.get(row.name, AgentStatus.IDLE)) for row in rows]

    # ==================================================================
    # CC1 路由等价：16 个原工具 → 对应命令，逐字段比对
    # ==================================================================

    async def test_cc1_get_time(self):
        """get_time ↔ `time get` / `time get --tz UTC`。"""
        direct = get_time()
        via = await self._call("time get", _context=self._ctx())
        assert via["success"] is True
        # 时间串会随时间变，只能比「格式相同 + 都带本地时区前缀」这一层。
        assert direct.keys() == via.keys()
        assert "当前本地时间" in via["message"]

        direct_tz = get_time(timezone="UTC")
        via_tz = await self._call("time get --tz UTC", _context=self._ctx())
        assert via_tz["success"] is True
        assert "UTC" in via_tz["message"]
        assert direct_tz.keys() == via_tz.keys()

    async def test_cc1_get_dept_info_ls(self):
        """get_dept_info(无参) ↔ `dept ls`。"""
        direct = await get_dept_info(_context=self._ctx())
        via = await self._call("dept ls", _context=self._ctx())
        assert via["success"] is True
        assert _as_tool_result(direct)["dept"] == via["dept"]

    async def test_cc1_get_dept_info_get(self):
        """get_dept_info(dept_id) ↔ `dept get <名称>` 与 `dept get <id>`。"""
        root = await gtDeptManager.get_dept_by_name(self.team_id, "root")
        assert root is not None

        direct = await get_dept_info(dept_id=root.id, _context=self._ctx())
        by_name = await self._call("dept get root", _context=self._ctx())
        by_id = await self._call(f"dept get {root.id}", _context=self._ctx())

        assert by_name["success"] is True and by_id["success"] is True
        assert _as_tool_result(direct)["dept"] == by_name["dept"] == by_id["dept"]

    async def test_cc1_save_dept_create(self):
        """save_dept(新建) ↔ `dept create <json>`。"""
        payload = {
            "name": "cc1_dept",
            "responsibility": "cc1 用",
            "manager_name": "mgr1",
            "member_names": ["mgr1", "charlie"],
            "parent_name": "root",
        }
        via = await self._call(
            f"dept create '{json.dumps(payload, ensure_ascii=False)}'", _context=self._ctx()
        )
        direct = await save_dept(
            name="cc1_dept_direct",
            responsibility="cc1 用",
            manager_name="mgr7",
            member_names=["mgr7", "mgr8"],
            parent_name="root",
            _context=self._ctx(),
        )
        assert via["success"] is True, via
        assert direct["success"] is True, direct
        # 等价性判据：除名字外每个序列化字段都一致（名字是用来区分两条用例的）。
        assert via["dept"]["manager"] == "mgr1"
        assert direct["dept"]["manager"] == "mgr7"
        # members 里各自包含自己的负责人 + charlie（顺序由底层序列化决定，只比集合）。
        assert set(via["dept"]["members"]) == {"mgr1", "charlie"}
        assert set(direct["dept"]["members"]) == {"mgr7", "mgr8"}
        assert set(via["dept"].keys()) == set(direct["dept"].keys()), "字段集合必须一致"

    async def test_cc1_save_dept_update_is_patch(self):
        """save_dept(更新) ↔ `dept update`，且是 PATCH（见 CC5）。"""
        row = await gtDeptManager.get_dept_by_name(self.team_id, "cc1_dept")
        assert row is not None
        via = await self._call(
            "dept update cc1_dept '{\"responsibility\":\"改过的职责\"}'", _context=self._ctx()
        )
        assert via["success"] is True, via

        saved = await gtDeptManager.get_dept_by_name(self.team_id, "cc1_dept")
        assert saved is not None
        assert saved.responsibility == "改过的职责"
        # 未出现的字段必须保持原值（PATCH）。
        assert saved.name == "cc1_dept"
        assert saved.manager_id == self.agent_ids["mgr1"]
        assert sorted(saved.agent_ids) == sorted([self.agent_ids["mgr1"], self.agent_ids["charlie"]])

    async def test_cc1_get_agent_info_ls(self, monkeypatch):
        """get_agent_info(无参) ↔ `agent ls`：每人一条、字段集合一致。"""
        runtimes = await self._fake_runtime_agents()
        monkeypatch.setattr(agentService, "get_team_agents", lambda team_id: runtimes)
        ctx = self._ctx()

        direct = await get_agent_info(_context=ctx)
        via = await self._call("agent ls", _context=ctx)
        assert via["success"] is True
        # 先证明夹具真的喂进了数据：否则「两边都空」会让下面的相等断言恒真。
        assert len(via["agents"]) == len(runtimes) > 0
        assert [a["name"] for a in via["agents"]] == [a["name"] for a in direct["agents"]]
        assert all(a.keys() == direct["agents"][0].keys() for a in via["agents"])

    async def test_cc1_get_agent_info_get(self, monkeypatch):
        """get_agent_info(name) ↔ `agent get <名称>` 与 `agent get <id>`。"""
        runtimes = await self._fake_runtime_agents()
        monkeypatch.setattr(agentService, "get_team_agents", lambda team_id: runtimes)
        ctx = self._ctx()
        direct = await get_agent_info(name="bob", _context=ctx)
        by_name = await self._call("agent get bob", _context=ctx)
        by_id = await self._call(f"agent get {self.agent_ids['bob']}", _context=ctx)
        assert by_name["success"] is True and by_id["success"] is True
        assert by_name["agent"] == _as_tool_result(direct)["agent"]
        assert by_id["agent"] == _as_tool_result(direct)["agent"], "`agent get <id>` 必须按 id 定位到同一个成员"

    async def test_cc1_save_agent_create(self):
        """save_agent(新建) ↔ `agent create <json>`。"""
        via = await self._call(
            "agent create '{\"name\":\"cc1_agent\",\"role_template_name\":\"test_role\","
            "\"driver\":\"native\"}'",
            _context=self._ctx(),
        )
        assert via["success"] is True, via
        saved = await gtAgentManager.get_agent(self.team_id, "cc1_agent", status=None)
        assert saved is not None
        assert saved.role_template_id == self.role_template_id
        assert saved.driver.value == "native"

    async def test_cc1_save_agent_update_is_patch(self):
        """save_agent(更新) ↔ `agent update`，且未出现的字段保持原值。"""
        before = await gtAgentManager.get_agent(self.team_id, "cc1_agent", status=None)
        assert before is not None
        via = await self._call(
            "agent update cc1_agent '{\"model\":\"gpt-4o-mini\"}'", _context=self._ctx()
        )
        assert via["success"] is True, via
        after = await gtAgentManager.get_agent(self.team_id, "cc1_agent", status=None)
        assert after is not None
        assert after.model == "gpt-4o-mini"
        assert after.name == "cc1_agent"
        assert after.driver == before.driver
        assert after.role_template_id == before.role_template_id

    async def test_cc1_wake_up_agent(self, monkeypatch):
        """wake_up_agent ↔ `agent wake`：非 FAILED 时两侧都给同样的拒绝文案。"""
        bob = await gtAgentManager.get_agent(self.team_id, "bob")
        assert bob is not None
        monkeypatch.setattr(
            agentService, "get_team_agents", lambda team_id: [_FakeRuntimeAgent(bob, AgentStatus.IDLE)]
        )
        direct = await wake_up_agent("bob", _context=self._ctx())
        via = await self._call("agent wake bob", _context=self._ctx())
        assert via["success"] is False
        assert via["message"] == direct["message"]

    async def test_cc1_wake_up_agent_success_path(self, monkeypatch):
        """wake_up_agent 成功分支 ↔ `agent wake`：两侧都返回 success 且消息一致。"""
        bob = await gtAgentManager.get_agent(self.team_id, "bob")
        assert bob is not None
        fake = _FakeRuntimeAgent(bob, AgentStatus.FAILED)
        monkeypatch.setattr(agentService, "get_team_agents", lambda team_id: [fake])
        direct = await wake_up_agent("bob", _context=self._ctx())
        via = await self._call("agent wake bob", _context=self._ctx())
        assert direct["success"] is True and via["success"] is True
        assert via["message"] == direct["message"]
        assert fake.resumed is True

    async def test_cc1_start_chat(self):
        """start_chat ↔ `agent dm`：都建出同一个单聊房间。"""
        ctx = self._ctx()
        # `start_chat` 读 DB（不读运行态），所以不需要桩 runtime。
        # 第一次应新建，第二次应命中「已存在」——两侧关键字段必须一致。
        direct = await self._call("agent dm charlie", _context=ctx)
        via = await self._call("agent dm charlie", _context=ctx)
        assert direct["success"] is True, direct
        assert via["success"] is True, via
        assert via["room"]["name"] == direct["room"]["name"]
        assert set(via["room"]["members"]) == set(direct["room"]["members"])
        # 第二次必须是「已存在」而不是重复创建。
        assert via["is_new_created"] is False

    async def test_cc1_get_room_info_ls_and_get(self):
        """get_room_info ↔ `room ls` / `room get <名称>` / `room get <id>`。"""
        ctx = self._ctx()
        await self.create_room(TEAM, "cc1_room", ["alice", "bob", "charlie"], max_rounds=7)
        row = await gtRoomManager.get_room_by_team_and_name(self.team_id, "cc1_room")
        assert row is not None

        direct = await get_room_info(name="cc1_room", _context=ctx)
        by_name = await self._call("room get cc1_room", _context=ctx)
        by_id = await self._call(f"room get {row.id}", _context=ctx)
        assert by_name["room"] == _as_tool_result(direct)["room"]
        assert by_id["room"] == _as_tool_result(direct)["room"], "`room get <id>` 必须按 id 定位到同一个房间"

        direct_ls = await get_room_info(_context=ctx)
        via_ls = await self._call("room ls", _context=ctx)
        assert [r["room_name"] for r in via_ls["rooms"]] == [
            r["room_name"] for r in direct_ls["rooms"]
        ]

    async def test_cc1_save_room_create(self):
        """save_room(新建) ↔ `room create <json>`。"""
        via = await self._call(
            "room create '{\"name\":\"cc1_room2\",\"member_names\":[\"alice\",\"bob\"],"
            "\"max_rounds\":5}'",
            _context=self._ctx(),
        )
        assert via["success"] is True, via
        row = await gtRoomManager.get_room_by_team_and_name(self.team_id, "cc1_room2")
        assert row is not None
        assert row.max_rounds == 5
        assert sorted(row.agent_ids) == sorted([self.agent_ids["alice"], self.agent_ids["bob"]])

    async def test_cc1_save_room_update_is_patch(self):
        """save_room(更新) ↔ `room update`，且是 PATCH。"""
        before = await gtRoomManager.get_room_by_team_and_name(self.team_id, "cc1_room2")
        assert before is not None
        via = await self._call(
            "room update cc1_room2 '{\"max_rounds\":12}'", _context=self._ctx()
        )
        assert via["success"] is True, via
        after = await gtRoomManager.get_room_by_team_and_name(self.team_id, "cc1_room2")
        assert after is not None
        assert after.max_rounds == 12
        assert after.name == "cc1_room2"
        assert sorted(after.agent_ids) == sorted(before.agent_ids)

    async def test_cc1_list_role_templates(self):
        """list_role_templates ↔ `role ls` / `role ls --kw`：同样条数、同样名字集合。"""
        ctx = self._ctx()
        direct = await list_role_templates(_context=ctx)
        via = await self._call("role ls", _context=ctx)
        assert via["success"] is True
        names_direct = sorted(t["name"] for t in direct["role_templates"])
        names_via = sorted(t["name"] for t in via["role_templates"])
        assert names_via == names_direct

        direct_kw = await list_role_templates(keywords=["test_role"], _context=ctx)
        via_kw = await self._call("role ls --kw test_role", _context=ctx)
        assert sorted(t["name"] for t in via_kw["role_templates"]) == sorted(
            t["name"] for t in direct_kw["role_templates"]
        )

    async def test_cc1_get_role_template(self):
        """get_role_template ↔ `role get`。"""
        ctx = self._ctx()
        direct = await get_role_template(role_name="test_role", _context=ctx)
        via = await self._call("role get test_role", _context=ctx)
        assert via["success"] is True
        assert via["role_template"] == _as_tool_result(direct)["role_template"]

    async def test_cc1_save_role_template_create_and_update(self):
        """save_role_template ↔ `role create` / `role update`（update 是 PATCH）。"""
        via = await self._call(
            "role create '{\"name\":\"cc1_role\",\"soul\":\"be careful\",\"type\":\"USER\"}'",
            _context=self._ctx(),
        )
        assert via["success"] is True, via
        assert via["role_template"]["soul"] == "be careful"

        upd = await self._call(
            "role update cc1_role '{\"soul\":\"be very careful\"}'", _context=self._ctx()
        )
        assert upd["success"] is True, upd
        assert upd["role_template"]["soul"] == "be very careful"
        assert upd["role_template"]["name"] == "cc1_role"
        assert upd["role_template"]["type"] == "USER"

    async def test_cc1_delete_dept(self):
        """delete_dept ↔ `dept rm`：一次调用即删。

        #196 P2 起 console **不再自造 `impact`**：原工具返回体已有 `deleted` /
        `would_delete`（P1 加的），再叠一份等于同一件事说两遍。判据改成看原工具的
        `deleted` 清单 —— 它才是「删掉了什么」的唯一来源。
        """
        via = await self._call(
            f"dept create '{{\"name\":\"cc1_dept_rm\",\"responsibility\":\"x\","
            f"\"manager_name\":\"mgr3\",\"parent_name\":\"root\"}}'",
            _context=self._ctx(),
        )
        assert via["success"] is True, via

        result = await self._call("dept rm cc1_dept_rm", _context=self._ctx())
        assert result["success"] is True, result
        assert any(entry["name"] == "cc1_dept_rm" for entry in result["deleted"]), result
        assert await gtDeptManager.get_dept_by_name(self.team_id, "cc1_dept_rm") is None

    async def test_cc1_delete_role_template(self):
        """delete_role_template ↔ `role rm`：一次调用即删。

        `delete_role_template` 的返回体**没有 `deleted` 字段**（只有 `message` 与
        `role_template`），所以这里只能查库 + 看 message。#196 P2 不再叠 `impact`。
        """
        via = await self._call(
            "role create '{\"name\":\"cc1_role_rm\",\"soul\":\"tmp\",\"type\":\"USER\"}'",
            _context=self._ctx(),
        )
        assert via["success"] is True, via

        result = await self._call("role rm cc1_role_rm", _context=self._ctx())
        assert result["success"] is True, result
        assert "cc1_role_rm" in result["message"], result
        assert await gtRoleTemplateManager.get_role_template_by_name("cc1_role_rm") is None

    async def test_cc1_delete_room(self):
        """delete_room ↔ `room rm`：一次调用即删（返回体看原工具的 `deleted`）。"""
        via = await self._call(
            "room create '{\"name\":\"cc1_room_rm\",\"member_names\":[\"alice\",\"mgr8\"]}'",
            _context=self._ctx(),
        )
        assert via["success"] is True, via

        result = await self._call("room rm cc1_room_rm", _context=self._ctx())
        assert result["success"] is True, result
        assert any(entry["name"] == "cc1_room_rm" for entry in result["deleted"]), result
        assert await gtRoomManager.get_room_by_team_and_name(self.team_id, "cc1_room_rm") is None

    async def test_cc1_reload_forwards_and_predicate_matches(self):
        """reload_team ↔ `reload`：**无条件转发 + 自中断判定**可测；真中断路径不可在进程内测。

        `reload_team` 内部 `create_task(hot_reload)` 后挂在一个永不完成的 future 上，
        真正的中断由 `AgentTurnRunner` 在 handler 外触发。#165 删掉 confirm 闸门后，
        `reload` 不再有「带 token 才执行」的前置条件，本用例证明能证明的三件事：
        ① `reload` 直接抛出 `reload_team` 的转发（不带任何 token）；
        ② 动态自中断判定器对 `reload` 返回 True（D6 要的语义）；
        ③ 非 reload 命令返回 False。
        真中断 + 重启后自动判成功，需要真实运行时重启，列为「未验证项」写进报告。
        """
        # ① 无条件转发：不带 --confirm 也直接抛 ForwardToolCall 给 reload_team。
        #
        # **必须直接调 `console()` 而不是走 `self._call`**：`_call` 会一路执行到第 2 跳，
        # 而 `reload_team` 内部 `create_task(hot_reload)` 后挂在一个永不完成的 future 上
        # （设计如此，见 `_handle_reload` docstring）——走完整路径会让本用例永久挂起。
        # 这里要验的是「console 层不再拦 confirm、直接抛转发信号」，停在抛点正好。
        from service.funcToolService.consoleTool import console as _console

        with pytest.raises(ForwardToolCall) as exc_info:
            await _console("reload", _context=self._ctx())
        assert exc_info.value.tool_name == "reload_team"

        # ② 判定器：reload 必中断（不再依赖 token）。
        assert console_triggers_self_interrupt(_tool_call("reload")) is True, (
            "reload 必须被判为自中断（D6）"
        )
        # ③ 非 reload 命令永不自中断（静态标志若被误设会在这里露出来）。
        assert console_triggers_self_interrupt(_tool_call("dept ls")) is False
        assert console_triggers_self_interrupt(_tool_call("help")) is False
        assert console_triggers_self_interrupt(_tool_call("dept rm root")) is False

    # ==================================================================
    # CC2 权限真咬合（D3，本单唯一不可降级项）
    #
    # #160 之后的判据口径变了，这不是放松而是**收紧**：
    #   旧：console 自己 `_assert_permitted` → 返回 `error_code=permission_denied`
    #       + `legacy_tool` + `required_category`（一份独立实现，可与注册表漂移）；
    #   新：console 只抛 `ForwardToolCall`，第 2 跳由 `execute_tool_call` 查
    #       `registered.enabled` → `工具无权限使用: <目标工具>`（与直接调原工具**同一份**判定）。
    # 所以断言改成「注册表那句拒绝文案」+「DB 真没被改」，并且**故意不再断言**
    # `permission_denied` —— 那个字段随实现一起消失了，继续断言它等于要求把重复实现加回来。
    # ==================================================================

    async def test_cc2_basic_only_caller_is_denied_admin_command(self):
        """只允许 BASIC 的调用者跑 `agent create` → **被注册表拒**，且 DB 里没有新成员。"""
        before = await gtAgentManager.get_agent(self.team_id, "cc2_forbidden", status=None)
        assert before is None

        result = await self._call(
            "agent create '{\"name\":\"cc2_forbidden\",\"role_template_name\":\"test_role\"}'",
            _context=self._ctx("bob"),
        )
        assert result["success"] is False
        # 拒绝文案来自统一入口（工具名是**转发目标**，不是 console 子命令）。
        assert "工具无权限使用: save_agent" in result["message"], result
        # 反证：这个返回体不是 console 自己的错误格式（那些都带 error_code）。
        assert "error_code" not in result, f"拒绝应来自注册表而不是 console：{result}"
        # 光断言返回体不够：必须证明**真的没落库**（拒绝发生在执行之前）。
        assert await gtAgentManager.get_agent(self.team_id, "cc2_forbidden", status=None) is None

    async def test_cc2_basic_only_caller_is_allowed_read_command(self):
        """同一个 BASIC 调用者跑 `dept ls` → 必须通过（console 是 BASIC，D3 前提）。"""
        result = await self._call("dept ls", _context=self._ctx("bob"))
        assert result["success"] is True
        assert result["dept"]["dept_name"] == "root"

    async def test_cc2_root_leader_can_run_admin_command(self):
        """根主管（运行时自动获 Admin）跑同一条 `agent create` → 通过。

        这条是**上一条的阳性对照**：如果只测「被拒」，那么把权限校验写成永远拒绝也能过。
        两条一起才说明拒绝是「按权限判的」，不是「一律拒」。
        """
        result = await self._call(
            "agent create '{\"name\":\"cc2_allowed\",\"role_template_name\":\"test_role\","
            "\"driver\":\"native\"}'",
            _context=self._ctx("alice"),
        )
        assert result["success"] is True, result

    @pytest.mark.parametrize(
        "command,expected_tool",
        [
            ("role create '{\"name\":\"x\",\"soul\":\"y\"}'", "save_role_template"),
            # `list_role_templates` 在 CATEGORY_CONFIG 里就是 ADMIN（不是 BASIC），
            # 所以 `role ls` 必须按 ADMIN 校验——这条正是「按等价原工具查表」的体现。
            ("role ls", "list_role_templates"),
            ("agent update bob '{\"model\":\"m\"}'", "save_agent"),
            ("room update cc1_room '{\"max_rounds\":3}'", "save_room"),
        ],
    )
    async def test_cc2_every_admin_subcommand_is_denied_for_basic_caller(
        self, command, expected_tool
    ):
        """**逐条**验证管理类子命令对 BASIC 调用者都关门（不是只测 save_agent）。

        单一用例容易漏掉「加新命令时忘填 legacy_tool」——这里按命令表遍历，
        新增管理命令若没被判为 ADMIN，这条会红。
        """
        result = await self._call(command, _context=self._ctx("charlie"))
        assert result["success"] is False
        assert f"工具无权限使用: {expected_tool}" in result["message"], result

    async def test_cc2_admin_delete_and_reload_are_denied_without_confirm_gate(self):
        """ADMIN 删除/重载命令对 BASIC 调用者关门，**且拒绝发生在执行之前**。

        #165 删掉 confirm 闸门后，这条判据比原来更重要：以前「不带 token → 只拿到
        `confirm_required`」这一步会先把调用挡在权限判定之外，现在没有这层缓冲，
        **每一次 console 删除/重载调用都会立刻走到第 2 跳的注册表鉴权**。所以这里
        必须证明「拒绝 + 目标仍在」，而不是只看返回体。
        """
        charlie = self._ctx("charlie")
        alice = self._ctx()

        # 用一次性目标，绝不拿 root / 已复用的房间当靶子（万一判据失效也只是丢一个空壳）。
        created = await self._call(
            "dept create '{\"name\":\"cc2_rm_dept\",\"responsibility\":\"tmp\","
            "\"manager_name\":\"mgr5\",\"parent_name\":\"root\"}'",
            _context=alice,
        )
        assert created["success"] is True, created
        await self.create_room(TEAM, "cc2_rm_room", ["mgr6", "mgr7"], max_rounds=3)

        for command, expected_tool in (
            ("dept rm cc2_rm_dept", "delete_dept"),
            ("room rm cc2_rm_room", "delete_room"),
            ("reload", "reload_team"),
        ):
            result = await self._call(command, _context=charlie)
            assert result["success"] is False, (command, result)
            assert f"工具无权限使用: {expected_tool}" in result["message"], (command, result)

        # 真正重要的一条：被拒之后目标必须还在（拒绝发生在删除之前）。
        assert await gtDeptManager.get_dept_by_name(self.team_id, "cc2_rm_dept") is not None
        assert await gtRoomManager.get_room_by_team_and_name(self.team_id, "cc2_rm_room") is not None

    @pytest.mark.parametrize(
        "command,expected_tool",
        [
            ("dept ls", "get_dept_info"),
            ("agent ls", "get_agent_info"),
            ("room ls", "get_room_info"),
            ("time get", "get_time"),
            ("agent wake bob", "wake_up_agent"),
            ("agent dm charlie", "start_chat"),
        ],
    )
    async def test_cc2_every_basic_subcommand_is_allowed_for_basic_caller(
        self, command, expected_tool
    ):
        """反过来：所有 BASIC 子命令对 BASIC 调用者都必须放行（负向对照）。

        没有这条，把判定写成「只有 Admin 能过」也照样能让上一条绿。
        """
        result = await self._call(command, _context=self._ctx("charlie"))
        assert "工具无权限使用" not in str(result.get("message", "")), (
            f"`{command}` 等价于 {expected_tool}（BASIC），不该被拒：{result}"
        )

    async def test_cc2_fail_closed_when_legacy_tool_missing(self, monkeypatch):
        """命令表漏填 `legacy_tool` 时必须 **拒绝**，不能「没填就放行」。

        fail-closed 是 D3 的兜底：将来加命令时漏填等价工具名，#160 之后转发目标为空，
        第 2 跳在注册表里查不到 → 按「未知工具」拒绝（而不是静默执行 console 自己）。
        """
        from dataclasses import replace as dc_replace

        broken = dc_replace(cc.COMMAND_INDEX["agent create"], legacy_tool="")
        monkeypatch.setitem(cc.COMMAND_INDEX, "agent create", broken)

        result = await self._call(
            "agent create '{\"name\":\"cc2_failopen\",\"role_template_name\":\"test_role\"}'",
            _context=self._ctx("alice"),  # 连根主管也拒：这是内部错误，不是权限问题
        )
        assert result["success"] is False
        assert "未知工具" in result["message"], result
        assert await gtAgentManager.get_agent(self.team_id, "cc2_failopen", status=None) is None

    async def test_cc2_permission_checked_before_execution(self, monkeypatch):
        """权限校验必须发生在**执行之前**：用计数桩证明底层实现没被调用。

        只断言返回体「被拒」不够——实现可能「先执行再报无权限」，那样数据已经被改了。
        另外**必须带阳性对照**：如果桩根本进不到执行链路，`calls == []` 就是一条
        恒真的假判据（改坏实现也不会红）。
        """
        from constants import ToolVisibility
        from service.funcToolService.funcToolType import FuncTool

        calls: list[str] = []
        original_get = func_tool_core.get_func_tool

        async def _spy(**kwargs):
            calls.append(kwargs.get("name", ""))
            return {"success": True}  # 不落库：这是计数桩，不是被测行为

        def _spy_get(name):
            if name == "save_agent":
                # 桩必须保留真工具的基准可见性：console 路由按 `console_visible`
                # 过滤命令，默认 TOOL_ONLY 会让命令在解析期就被判为不可用。
                return FuncTool("save_agent", _spy, visibility=ToolVisibility.BOTH)
            return original_get(name)

        monkeypatch.setattr(func_tool_core, "get_func_tool", _spy_get)

        denied = await self._call(
            "agent create '{\"name\":\"cc2_spy\",\"role_template_name\":\"test_role\"}'",
            _context=self._ctx("bob"),
        )
        assert "工具无权限使用: save_agent" in denied["message"], denied
        assert calls == [], f"被拒的命令不得触达 save_agent，实际调用：{calls}"

        allowed = await self._call(
            "agent create '{\"name\":\"cc2_spy_ok\",\"role_template_name\":\"test_role\"}'",
            _context=self._ctx("alice"),
        )
        assert allowed["success"] is True, allowed
        assert calls == ["cc2_spy_ok"], f"放行时必须真的执行（否则上面那条是假判据）：{calls}"

    # ==================================================================
    # CC3 输入来源互斥（D1）
    # ==================================================================

    async def test_cc3_from_payload_without_payload_reports_missing_payload(self):
        """`--from payload` 但没给 `payload` → `missing_payload`（不是「解析失败」）。"""
        result = await self._call("agent create --from payload", _context=self._ctx())
        assert result["success"] is False
        assert result["error_code"] == "missing_payload"

    async def test_cc3_payload_given_without_from_reports_ambiguous(self):
        """给了 `payload` 却用内联 JSON → `ambiguous_input_source`。"""
        result = await self._call(
            "agent create '{\"name\":\"cc3_a\",\"role_template_name\":\"test_role\"}'",
            payload={"name": "cc3_a", "role_template_name": "test_role"},
            _context=self._ctx(),
        )
        assert result["success"] is False
        assert result["error_code"] == "ambiguous_input_source"

    async def test_cc3_payload_given_without_from_on_non_input_command(self):
        """`dept ls` 传了 payload → 同样 `ambiguous_input_source`（不许静默忽略）。"""
        result = await self._call("dept ls", payload={"x": 1}, _context=self._ctx())
        assert result["success"] is False
        assert result["error_code"] in {"ambiguous_input_source", "unknown_option"}

    async def test_cc3_from_payload_together_with_inline_json_is_ambiguous(self):
        """`--from payload` 与内联 JSON 同时出现 → `ambiguous_input_source`。"""
        result = await self._call(
            "agent create '{\"name\":\"cc3_b\"}' --from payload",
            payload={"name": "cc3_b", "role_template_name": "test_role"},
            _context=self._ctx(),
        )
        assert result["success"] is False
        assert result["error_code"] == "ambiguous_input_source"

    async def test_cc3_from_payload_happy_path(self):
        """`--from payload` + payload object → 正常创建（证明上面几条不是「一律拒绝」）。"""
        result = await self._call(
            "agent create --from payload",
            payload={"name": "cc3_ok", "role_template_name": "test_role", "driver": "native"},
            _context=self._ctx(),
        )
        assert result["success"] is True, result
        saved = await gtAgentManager.get_agent(self.team_id, "cc3_ok", status=None)
        assert saved is not None and saved.driver.value == "native"

    async def test_cc3_no_input_source_at_all(self):
        """create 什么都不给 → `missing_input_source`。"""
        result = await self._call("agent create", _context=self._ctx())
        assert result["success"] is False
        assert result["error_code"] == "missing_input_source"

    @pytest.mark.parametrize(
        "command,expected_code",
        [
            ("agent create --from local:cfg.json", "unsupported_input_source"),
            ("agent create --from <<'JSON'", "unsupported_input_source"),
            ("agent create --from http://x/y.json", "unsupported_input_source"),
        ],
    )
    async def test_cc3_unsupported_sources_are_rejected_explicitly(self, command, expected_code):
        """`local:` / heredoc / 未知来源 → 明确的 `unsupported_input_source`（D1 扩展点）。"""
        result = await self._call(command, _context=self._ctx())
        assert result["success"] is False
        assert result["error_code"] == expected_code
        assert result["supported"] == ["payload"], "必须告诉模型 v1 支持什么"

    async def test_cc3_invalid_inline_json(self):
        """内联 JSON 语法错误 → `invalid_source_json`（带行列号，便于模型自纠）。"""
        result = await self._call("agent create '{bad json'", _context=self._ctx())
        assert result["success"] is False
        assert result["error_code"] == "invalid_source_json"

    async def test_cc3_inline_json_must_be_object(self):
        """根节点不是 object（如数组）→ `invalid_payload_field`。"""
        result = await self._call("agent create '[1,2,3]'", _context=self._ctx())
        assert result["success"] is False
        assert result["error_code"] == "invalid_payload_field"

    # ==================================================================
    # CC4 删除保护（D4）
    #
    # 夹具注意：`roomService.create_room` 拒绝「成员集合完全相同」的第二个房间
    # （`ROOM_DUPLICATE`）。所以下面每个房间都用**不同的成员组合**——这是夹具约束，
    # 不是被测行为；不这样写，用例之间会因为房间名不同但成员相同而互相干扰。
    # ==================================================================

    async def test_cc4_room_rm_deletes_in_one_call_and_reports_deleted(self):
        """一次调用即真删，返回体带原工具的 `deleted` 清单。

        #196 P2 起 console 不再自造 `impact`（原工具已有 `deleted`/`would_delete`）。
        删除后查库证明真的删了（不能只看返回体）。
        """
        ctx = self._ctx()
        await self.create_room(TEAM, "cc4_room", ["bob", "charlie"], max_rounds=3)
        assert await gtRoomManager.get_room_by_team_and_name(self.team_id, "cc4_room") is not None

        result = await self._call("room rm cc4_room", _context=ctx)
        assert result["success"] is True, result
        assert any(entry["name"] == "cc4_room" for entry in result["deleted"]), result
        assert await gtRoomManager.get_room_by_team_and_name(self.team_id, "cc4_room") is None

    async def test_cc4_room_rm_unknown_target_reports_not_found(self):
        """不存在的目标 → 明确「未找到房间: name=…」，且不删任何东西。

        #196 P2 起 console 不再**预查**目标（旧实现先 `_resolve_room_by_target` 拿
        row，查不到自造 `not_found`），而是直接转发、由原工具报「未找到」。
        所以这里断言原工具那条文案与「没有 error_code」（业务失败无码，见报告
        「对外契约变更」一节），**不是** `not_found`。
        """
        result = await self._call("room rm cc4_no_such_room", _context=self._ctx())
        assert result["success"] is False
        assert "未找到房间: name=cc4_no_such_room" in result["message"], result
        assert await gtRoomManager.get_room_by_team_and_name(
            self.team_id, "cc4_no_such_room"
        ) is None

    async def test_cc4_confirm_option_is_gone(self):
        """`--confirm` 已不是合法选项 → `unknown_option`（#165 D 的负向判据）。

        没有这条，把 `options=("confirm",)` 加回去也照样全绿。
        """
        for command in ("room rm whatever --confirm abc123", "reload --confirm abc123"):
            result = await self._call(command, _context=self._ctx())
            assert result["success"] is False, (command, result)
            assert result["error_code"] == "unknown_option", (command, result)

    async def test_cc4_failure_result_carries_no_deletion_manifest(self):
        """失败体**不带任何「删掉了什么」清单**（旧缺陷的延续判据）。

        #165 E 的旧缺陷是「无条件 `{**result, "impact": ...}`」，会把影响范围挂到
        `{"success": False}` 上，让模型以为部分成功。#196 P2 删掉了 console 自造的
        `impact`，但**同一类错误可以再犯**：只要有人给成功路径加清单字段却忘了判
        `success`，症状就复现。所以判据从「`impact` 不在」升级为「任何清单字段都不在」
        —— 这样换个字段名也咬得住。
        """
        ctx = self._ctx()
        # 删一个 DEPT 房间：原工具会拒绝（DEPT 房间不允许删除），这里只验 impact 不被叠上。
        dept_room = await gtRoomManager.get_room_by_team_and_name(self.team_id, "root")
        if dept_room is None:
            pytest.skip("夹具里没有 root 房间，跳过")
        result = await self._call("room rm root", _context=ctx)
        assert result["success"] is False, result
        for manifest in ("impact", "deleted", "would_delete"):
            assert manifest not in result, f"失败体不得带 {manifest}：{result}"

    async def test_cc4_dept_rm_recursive_required_is_refused(self):
        """`dept rm` 有子部门且未加 `--recursive` → 被拒，且一点没删。

        **这条与 confirm 无关，必须保留**：它是「一次误触删掉一棵子树」的物理阻断，
        不随确认闸门一起移除。

        #196 P2 起错误码由 console 自造的 `recursive_required` 变成**服务层**的
        `DEPT_HAS_CHILDREN`（`deptService.delete_dept` 抛 `TogoException`）：console
        不再预查子部门，冲突由服务层判，错误码随之外移。文案也变了——服务层不再报
        「N 个子部门」，只是拒绝。详见报告「对外契约变更」一节。
        """
        ctx = self._ctx()
        await self._call(
            "dept create '{\"name\":\"cc4_parent\",\"responsibility\":\"p\","
            "\"manager_name\":\"mgr2\",\"parent_name\":\"root\"}'",
            _context=ctx,
        )
        await self._call(
            "dept create '{\"name\":\"cc4_child\",\"responsibility\":\"c\","
            "\"manager_name\":\"mgr4\",\"parent_name\":\"cc4_parent\"}'",
            _context=ctx,
        )
        result = await self._call("dept rm cc4_parent", _context=ctx)
        assert result["success"] is False
        assert result["error_code"] == "DEPT_HAS_CHILDREN", result
        # 「拒绝了」必须配「什么都没删」：只看错误码挡不住「先删一半再报错」。
        assert await gtDeptManager.get_dept_by_name(self.team_id, "cc4_parent") is not None
        assert await gtDeptManager.get_dept_by_name(self.team_id, "cc4_child") is not None

    async def test_cc4_dept_rm_recursive_lists_children_then_deletes(self):
        """`dept rm --recursive` → **一次调用**父子都删，`deleted` 里列出被删的子部门名。

        自建数据（不依赖上一条用例留下的 `cc4_parent`）：用例之间共享夹具状态是
        `-k` 子集跑假红的来源，这里不再新增这种依赖。
        """
        ctx = self._ctx()
        # 自建两个独占负责人：夹具里 mgr1..mgr9 已被别的用例当部门负责人占用
        # （deptService 规定一个成员只能担任一个部门的负责人），直接复用会撞
        # DEPT_MANAGER_ALREADY_LEADS。用例自建就能独立跑，不依赖执行顺序。
        for agent_name in ("cc4r_mgr_a", "cc4r_mgr_b"):
            created_agent = await self._call(
                f"agent create '{{\"name\":\"{agent_name}\","
                f"\"role_template_name\":\"test_role\",\"driver\":\"tsp\"}}'",
                _context=ctx,
            )
            assert created_agent["success"] is True, (agent_name, created_agent)

        for name, parent, mgr in (
            ("cc4r_parent", "root", "cc4r_mgr_a"),
            ("cc4r_child", "cc4r_parent", "cc4r_mgr_b"),
        ):
            created = await self._call(
                f"dept create '{{\"name\":\"{name}\",\"responsibility\":\"x\","
                f"\"manager_name\":\"{mgr}\",\"parent_name\":\"{parent}\"}}'",
                _context=ctx,
            )
            assert created["success"] is True, (name, created)
        result = await self._call("dept rm cc4r_parent --recursive", _context=ctx)
        assert result["success"] is True, result
        deleted_names = {entry["name"] for entry in result["deleted"]}
        assert "cc4r_child" in deleted_names, f"删除清单必须列出被一并删掉的子部门：{deleted_names}"
        assert await gtDeptManager.get_dept_by_name(self.team_id, "cc4r_parent") is None
        assert await gtDeptManager.get_dept_by_name(self.team_id, "cc4r_child") is None

    # ==================================================================
    # CC5 PATCH 语义（D4/draft §4.3）
    # ==================================================================

    async def test_cc5_dept_update_only_changes_given_field(self):
        """`dept update` 只改 payload 里出现的字段，其余全保持。"""
        ctx = self._ctx()
        await self._call(
            "dept create '{\"name\":\"cc5_dept\",\"responsibility\":\"原始职责\","
            "\"manager_name\":\"mgr6\",\"member_names\":[\"mgr6\",\"mgr9\"],"
            "\"parent_name\":\"root\"}'",
            _context=ctx,
        )
        before = await gtDeptManager.get_dept_by_name(self.team_id, "cc5_dept")
        assert before is not None

        result = await self._call(
            "dept update cc5_dept '{\"manager_name\":\"charlie\"}'", _context=ctx
        )
        assert result["success"] is True, result
        after = await gtDeptManager.get_dept_by_name(self.team_id, "cc5_dept")
        assert after is not None
        assert after.manager_id == self.agent_ids["charlie"], "出现的字段必须生效"
        assert after.responsibility == "原始职责", "未出现的字段必须保持原值"
        assert after.name == "cc5_dept"
        assert after.parent_id == before.parent_id

    async def test_cc5_dept_update_array_field_replaces_wholesale(self):
        """数组字段一旦出现就是**全量替换**（不是追加/合并）。"""
        ctx = self._ctx()
        created = await self._call(
            "dept create '{\"name\":\"cc5_dept_arr\",\"responsibility\":\"r\","
            "\"manager_name\":\"mgr6\",\"member_names\":[\"mgr6\",\"mgr9\"],"
            "\"parent_name\":\"root\"}'",
            _context=ctx,
        )
        assert created["success"] is True, created

        result = await self._call(
            "dept update cc5_dept_arr '{\"member_names\":[\"mgr9\"]}'", _context=ctx
        )
        assert result["success"] is True, result
        after = await gtDeptManager.get_dept_by_name(self.team_id, "cc5_dept_arr")
        assert after is not None
        # 全量替换：只留 mgr9（负责人会被 upsert_dept 自动补回，所以负责人在不在
        # 名单里取决于服务层策略；这里断言「mgr6 之外的旧成员没被保留成追加」）。
        assert self.agent_ids["mgr9"] in after.agent_ids
        assert len(after.agent_ids) <= 2, f"必须是全量替换而不是追加：{after.agent_ids}"

    async def test_cc5_agent_update_preserves_unmentioned_fields(self):
        """`agent update` 未出现的字段（driver / role_template / allow_tools）保持不变。"""
        ctx = self._ctx()
        await self._call(
            "agent create '{\"name\":\"cc5_agent\",\"role_template_name\":\"test_role\","
            "\"driver\":\"claude_sdk\",\"model\":\"m1\",\"allow_tools\":[\"Category:Read\"]}'",
            _context=ctx,
        )
        before = await gtAgentManager.get_agent(self.team_id, "cc5_agent", status=None)
        assert before is not None
        assert before.driver.value == "claude_sdk"

        result = await self._call("agent update cc5_agent '{\"model\":\"m2\"}'", _context=ctx)
        assert result["success"] is True, result
        after = await gtAgentManager.get_agent(self.team_id, "cc5_agent", status=None)
        assert after is not None
        assert after.model == "m2"
        assert after.driver.value == "claude_sdk", "未出现的 driver 不能被打回默认值"
        assert after.role_template_id == before.role_template_id
        assert after.allow_tools == before.allow_tools

    async def test_cc5_room_update_preserves_members_and_topic(self):
        """`room update` 只改 max_rounds 时，成员与话题保持不变。"""
        ctx = self._ctx()
        await self._call(
            "room create '{\"name\":\"cc5_room\",\"member_names\":[\"alice\",\"mgr7\"],"
            "\"initial_topic\":\"原始话题\",\"max_rounds\":3}'",
            _context=ctx,
        )
        before = await gtRoomManager.get_room_by_team_and_name(self.team_id, "cc5_room")
        assert before is not None

        result = await self._call("room update cc5_room '{\"max_rounds\":9}'", _context=ctx)
        assert result["success"] is True, result
        after = await gtRoomManager.get_room_by_team_and_name(self.team_id, "cc5_room")
        assert after is not None
        assert after.max_rounds == 9
        assert after.initial_topic == "原始话题", "未出现的 initial_topic 必须保持原值"
        assert sorted(after.agent_ids) == sorted(before.agent_ids)

    async def test_cc5_role_update_preserves_type_and_i18n(self):
        """`role update` 只改 soul 时，type / i18n / name 保持不变。"""
        ctx = self._ctx()
        await self._call(
            "role create '{\"name\":\"cc5_role\",\"soul\":\"s1\",\"type\":\"USER\","
            "\"i18n\":{\"display_name\":{\"zh-CN\":\"角色甲\"}}}'",
            _context=ctx,
        )
        before = await gtRoleTemplateManager.get_role_template_by_name("cc5_role")
        assert before is not None

        result = await self._call("role update cc5_role '{\"soul\":\"s2\"}'", _context=ctx)
        assert result["success"] is True, result
        after = await gtRoleTemplateManager.get_role_template_by_name("cc5_role")
        assert after is not None
        assert after.soul == "s2"
        assert after.type == RoleTemplateType.USER
        assert after.i18n == before.i18n, "未出现的 i18n 必须保持原值"

    # ==================================================================
    # #198 §2.3 SAVE 输入契约：配置输入可选，`--选项` 是一等公民
    # ==================================================================

    async def test_cc6_create_with_options_only(self):
        """create 只给 `--选项`（无内联 JSON / payload）→ 正常创建。"""
        ctx = self._ctx()
        # 夹具里的 mgr1..mgr9 已被别的用例当部门负责人占用（一个成员只能带一个部门），
        # 自建负责人让本用例与执行顺序无关。
        created_agent = await self._call(
            "agent create '{\"name\":\"cc6_mgr\",\"role_template_name\":\"test_role\","
            "\"driver\":\"tsp\"}'",
            _context=ctx,
        )
        assert created_agent["success"] is True, created_agent

        result = await self._call(
            "dept create --name cc6_dept --responsibility 'cc6 用' "
            "--manager_name cc6_mgr --member_names cc6_mgr --parent_name root",
            _context=ctx,
        )
        assert result["success"] is True, result
        saved = await gtDeptManager.get_dept_by_name(self.team_id, "cc6_dept")
        assert saved is not None
        assert saved.responsibility == "cc6 用"
        assert saved.manager_id is not None

    async def test_cc6_update_with_options_only(self):
        """update 只给 `--选项` → 按 PATCH 语义只改该字段。"""
        result = await self._call(
            "dept update cc6_dept --responsibility 'cc6 改过'", _context=self._ctx()
        )
        assert result["success"] is True, result
        saved = await gtDeptManager.get_dept_by_name(self.team_id, "cc6_dept")
        assert saved is not None
        assert saved.responsibility == "cc6 改过"

    async def test_cc6_same_field_in_option_and_json_is_ambiguous(self):
        """同一字段同时出现在 `--选项` 与内联 JSON → ambiguous_input_source，且不写库。"""
        result = await self._call(
            "dept update cc6_dept --responsibility 'a' '{\"responsibility\":\"b\"}'",
            _context=self._ctx(),
        )
        assert result["success"] is False
        assert result["error_code"] == "ambiguous_input_source"
        saved = await gtDeptManager.get_dept_by_name(self.team_id, "cc6_dept")
        assert saved is not None and saved.responsibility == "cc6 改过", "冲突时不能写库"

    async def test_cc6_alias_conflict_uses_normalized_real_name(self):
        """别名 `--remove-field` 与 JSON 真名 `clear_fields` 按归一后的真名判冲突。"""
        result = await self._call(
            "task update 1 --remove-field reviewer_id '{\"clear_fields\":[\"reviewer_id\"]}'",
            _context=self._ctx(),
        )
        assert result["success"] is False
        assert result["error_code"] == "ambiguous_input_source"

    async def test_cc6_locator_in_options_is_ambiguous(self):
        """定位参数从 `--选项` 再给一次（与 `<target>` 来源重复）→ ambiguous_input_source。"""
        result = await self._call("dept update root --dept_id 1", _context=self._ctx())
        assert result["success"] is False
        assert result["error_code"] == "ambiguous_input_source"

    async def test_cc6_update_with_only_mechanism_option_is_no_update_action(self):
        """`--overwrite` 只是机制参数，不构成字段变更 → no_update_action。"""
        result = await self._call(
            "dept update cc6_dept --overwrite", _context=self._ctx()
        )
        assert result["success"] is False
        assert result["error_code"] == "no_update_action"

    async def test_cc6_blank_config_counts_as_no_update_action(self):
        """配置里只有空白值 = 没有可执行动作 → no_update_action（不静默成功）。"""
        result = await self._call(
            "dept update cc6_dept '{\"responsibility\":\"   \"}'", _context=self._ctx()
        )
        assert result["success"] is False
        assert result["error_code"] == "no_update_action"

    # ==================================================================
    # D5 / 错误格式：解析只用 shlex，绝不 eval
    # ==================================================================

    async def test_d5_unbalanced_quote_reports_syntax_error(self):
        """未闭合引号 → `invalid_command_syntax`（shlex 报错，不当成业务失败）。"""
        result = await self._call("dept get 'unclosed", _context=self._ctx())
        assert result["success"] is False
        assert result["error_code"] == "invalid_command_syntax"

    async def test_d5_no_eval_of_command_substitution(self):
        """`$(...)` / 反引号**不执行**，只当普通文本（D5：绝不 eval、不调 shell）。"""
        marker = "/tmp/console_eval_marker"
        if os.path.exists(marker):
            os.remove(marker)
        result = await self._call(f"dept get '$(touch {marker})'", _context=self._ctx())
        assert not os.path.exists(marker), "命令替换绝不能被执行"
        # 结果应是「未找到部门」，即把整段当成了一个普通名字。
        assert result["success"] is False

    async def test_unknown_command_gives_candidates(self):
        """未知命令 → `unknown_command` 且给出可用操作（draft §6 的友好错误）。"""
        result = await self._call("dept del x", _context=self._ctx())
        assert result["success"] is False
        assert result["error_code"] == "unknown_command"
        assert "ls" in result["message"] and "rm" in result["message"]

    async def test_unknown_option_rejected(self):
        """未知选项 → `unknown_option`（不是静默忽略）。"""
        result = await self._call("dept ls --nope", _context=self._ctx())
        assert result["success"] is False
        assert result["error_code"] == "unknown_option"

    async def test_help_does_not_require_permission(self):
        """`help` 不碰数据，任何身份都能看（否则没权限的人只能瞎试命令）。"""
        for name in ("alice", "bob", "charlie"):
            result = await self._call("help", _context=self._ctx(name))
            assert result["success"] is True
            assert "dept" in result["help"]


class TestConsoleDuplicateAgentName(_ConsoleViaRegistry, ServiceTestCase):
    """同名成员（离职名字被复用）时的保护面。

    ⚠️ **#196 P2 起保护范围收窄到 `agent update`**（详见下面各组用例）：

    为什么单独一组：`GtAgent` 的 `(team_id, name)` 是**非唯一**索引（离职成员的
    名字允许被新成员复用），而 `gtAgentManager.get_agent()` 底层是
    `peewee_async` 的 `aio_get` → `paginate(1,1)`，**不校验唯一性**：两行同名时
    它返回行序靠前的那一行（通常是先建的、已离职的那个）。按 id 定位「A」却拿到
    同名的「B」，返回体仍是 `success: True` —— 这是最贵的一类错：模型会当成成功
    继续往下做。draft §4.2 明确要求「同名冲突时提示改用 id」。

    这组用例是**回归钉**：改坏 `save_agent` 的同名消歧（原在
    `consoleParsing._resolve_agent_by_target`，已下沉到工具层），这里必须变红。

    ## 为什么 `agent get <同名>` 的保护被撤掉（P2 已拍板，不阻塞）

    P2 之前 console 对 `agent get` 额外挂了一个 `_guard_agent_get` post_process：
    转发后比对「运行时按名字解析出的 id」与「请求的 id」，不一致就报
    `ambiguous_target`。P2 把 console 收敛成**纯转发**后这个守卫删掉了，于是：

    - `agent get <id>` 的行为**由原工具 `get_agent_info` 决定**，它自带三档
      （无码「未找到成员: id=…」/ `member_not_on_board` / `agent_runtime_not_loaded`）；
    - `agent get <同名>` 走原工具的「按名匹配 name 或 display_name」，多行同名时
      取运行时列表里第一个 —— **静默成功，不再报 `ambiguous_target`**。

    这是本单的**对外契约变更**（报告「对外契约变更」一节逐条记）：`agent get` 是
    只读命令，取错人不会改库；而 `agent update` 才是「静默改错人」的真风险，所以
    同名保护只留在它身上（已下沉到 `save_agent` 的工具层保护）。
    """

    @classmethod
    async def async_setup_class(cls):
        db_path = cls._get_test_db_path()
        await ormService.startup(db_path)
        await persistenceService.startup()
        await agentService.startup()
        await roomService.startup()

        team = await gtTeamManager.save_team(GtTeam(name="dup_team"))
        cls.team_id = team.id
        template = await gtRoleTemplateManager.save_role_template(
            GtRoleTemplate(name="dup_role", soul="dup role")
        )
        cls.role_template_id = template.id
        await gtAgentManager.batch_save_agents(
            team.id,
            [
                GtAgent(team_id=team.id, name="boss", role_template_id=template.id),
                GtAgent(team_id=team.id, name="mate", role_template_id=template.id),
                # 先建离职行、后建在职行：让「取第一条」必定落到离职那一个，
                # 这样「按 id 拿错人」的缺陷才必然复现（而不是碰运气）。
                GtAgent(
                    team_id=team.id, name="dup", role_template_id=template.id,
                    employ_status=EmployStatus.OFF_BOARD, model="old-model",
                ),
                GtAgent(team_id=team.id, name="dup", role_template_id=template.id, model="new-model"),
            ],
        )
        rows = await gtAgentManager.get_team_all_agents(team.id)
        cls.rows = rows
        cls.boss_id = next(r.id for r in rows if r.name == "boss")
        mate_id = next(r.id for r in rows if r.name == "mate")
        cls.off_id = next(r.id for r in rows if r.name == "dup" and r.employ_status == EmployStatus.OFF_BOARD)
        cls.on_id = next(r.id for r in rows if r.name == "dup" and r.employ_status == EmployStatus.ON_BOARD)

        await gtTeamManager.set_team_enabled(team.id, False)
        await deptService.overwrite_dept_tree(
            team.id,
            GtDept(name="root", responsibility="root", manager_id=cls.boss_id, agent_ids=[cls.boss_id, mate_id]),
        )
        await gtTeamManager.set_team_enabled(team.id, True)

        # boss 是根部门负责人 ⇒ 该身份拿到的是带 ADMIN 工具的那份注册表。
        cls._prepare_registries(cls.boss_id)

    def _ctx(self) -> ToolCallContext:
        return ToolCallContext(agent_id=self.boss_id, team_id=self.team_id, chat_room=None)

    def _runtime_stub(self, monkeypatch, statuses: dict[int, AgentStatus] | None = None):
        """运行时桩只含 ON_BOARD —— 与真实 `agentService` 一致（它只加载在职）。"""
        rows = [r for r in self.rows if r.employ_status == EmployStatus.ON_BOARD]
        statuses = statuses or {}
        fakes = [_FakeRuntimeAgent(r, statuses.get(r.id, AgentStatus.IDLE)) for r in rows]
        monkeypatch.setattr(agentService, "get_team_agents", lambda team_id: fakes)
        return fakes


    async def test_agent_get_by_id_never_returns_a_different_agent(self, monkeypatch):
        """按 id 查「同名的另一个人」时，绝不能返回别人的详情。"""
        self._runtime_stub(monkeypatch)
        result = await self._call(f"agent get {self.off_id}", _context=self._ctx())
        assert result["success"] is False
        assert result["error_code"] == "member_not_on_board"
        assert str(self.off_id) in result["message"]

    async def test_agent_get_on_board_by_id_returns_that_agent(self, monkeypatch):
        """在职成员的 id 路径仍要正常（证明上面那条不是「id 路径一律拒绝」）。"""
        self._runtime_stub(monkeypatch)
        result = await self._call(f"agent get {self.on_id}", _context=self._ctx())
        assert result["success"] is True, result
        assert result["agent"]["id"] == self.on_id

    async def test_agent_get_by_duplicate_name_silently_takes_first_runtime_row(self, monkeypatch):
        """`agent get <同名>` 现在是**原工具的语义**：取运行时列表里第一个，不报歧义。

        这是 P2 的**契约变更**（不是缺陷被容忍）：console 不再对只读命令做同名守卫，
        详情见类 docstring。判据写成「钉住当前语义」而不是「期望它报错」——
        哪天有人把守卫加回去，这里会红并提醒同步报告。
        """
        self._runtime_stub(monkeypatch)
        result = await self._call("agent get dup", _context=self._ctx())
        assert result["success"] is True, result
        # 运行时桩只含在职行 ⇒ 取到的必然是 on_id，不会跑到离职那一行。
        assert result["agent"]["id"] == self.on_id

    async def test_agent_update_by_duplicate_name_is_ambiguous_and_does_not_write(self):
        """同名多行时 `agent update` 必须拒绝，且**库里一行都不能被改**。"""
        before = {r.id: r.model for r in await gtAgentManager.get_team_all_agents(self.team_id) if r.name == "dup"}
        result = await self._call("agent update dup '{\"model\":\"should-not-be-written\"}'", _context=self._ctx())
        assert result["success"] is False
        assert result["error_code"] == "ambiguous_target"
        after = {r.id: r.model for r in await gtAgentManager.get_team_all_agents(self.team_id) if r.name == "dup"}
        assert after == before, f"被拒的更新不得改库：{before} -> {after}"

    async def test_agent_update_by_id_targets_exactly_that_row(self):
        """按 id 更新必须只改那一行，同名兄弟行保持原值。"""
        result = await self._call(f"agent update {self.on_id} '{{\"model\":\"only-this-one\"}}'", _context=self._ctx())
        assert result["success"] is True, result
        rows = {r.id: r.model for r in await gtAgentManager.get_team_all_agents(self.team_id) if r.name == "dup"}
        assert rows[self.on_id] == "only-this-one"
        assert rows[self.off_id] != "only-this-one"

    # ⚠️ 原 `test_agent_get_by_id_rejects_when_runtime_resolves_to_other_id` 已删：
    # 它钉的是 `_guard_agent_get` 那个 post_process 一致性守卫，而 P2 把 console 收敛
    # 成纯转发后守卫已不存在（同名保护只留 `agent update`）。留着会变成「测一个不存在
    # 的函数」——把守卫的功能重新发明一遍就等于没删。代替它的是下面这条：
    async def test_agent_get_by_id_still_cannot_return_the_off_board_row(self, monkeypatch):
        """按 id 查离职那一行仍被原工具挡（`member_not_on_board`）—— 撤销守卫后不失守。

        这是删掉 `_guard_agent_get` 后的**替代判据**：守卫原本防的是「按 id 拿到别人的
        详情」，而原工具自己就挡得住离职行（运行时只加载在职）。所以撤守卫没有降级。
        """
        self._runtime_stub(monkeypatch)
        result = await self._call(f"agent get {self.off_id}", _context=self._ctx())
        assert result["success"] is False
        assert result["error_code"] == "member_not_on_board"
        assert str(self.off_id) in result["message"]


# ======================================================================
# #165 结构性护栏：A 死赋值 / B 转发目标唯一出口 / C 单次 resolve
#
# 这一组不是行为用例，而是**源码级判据**。理由：这三条改动的收益全是
# 「少一处可能的漂移」，而漂移本身不会让任何行为用例变红——只有把结构钉死，
# 后来的人把字面量写回去才会立刻被发现。
# ======================================================================

import re as _re  # noqa: E402
from pathlib import Path as _Path  # noqa: E402
from service.funcToolService import consoleTool as _console_tool_module  # noqa: E402


def _console_tool_source() -> str:
    return _Path(_console_tool_module.__file__).read_text(encoding="utf-8")


def test_p165_b_forward_tool_call_has_single_construction_site():
    """`ForwardToolCall` 只在 `_forward` 里构造一次（#165 B）。

    改动前有 4 处：`_forward` 一处 + `dept rm` / `room rm` / `role rm` 各手写一处字面量。
    那 3 处绕开了命令表的 `legacy_tool`，于是 `_forward` docstring 里「目标名只从
    命令表取」的不变式是假的——把 `dept rm` 的等价工具改成别的，静态护栏咬不住。
    判据：源码里 `ForwardToolCall(` 的出现次数必须 == 1，且那一次在 `_forward` 内。
    """
    source = _console_tool_source()
    sites = [m for m in _re.finditer(r"ForwardToolCall\(", source)]
    assert len(sites) == 1, (
        f"`ForwardToolCall` 构造点必须唯一（当前 {len(sites)} 处）："
        f"{[source[max(0, m.start()-60):m.start()+20] for m in sites]}"
    )
    # 那唯一一处必须在 _forward 函数体内（用函数起点定位，避免行号脆断）。
    forward_start = source.index("def _forward(")
    next_def = source.index("\ndef ", forward_start) if "\ndef " in source[forward_start:] else len(source)
    assert forward_start < sites[0].start() < next_def, "唯一的构造点必须在 `_forward` 内"


def test_p165_a_no_dead_rest_assignment():
    """`rest = tokens[...]` 必须只赋值一次（#165 A），且解析入口唯一。

    旧代码是三行连击：`rest = tokens[1:]` → `if reload: rest = tokens[1:]` →
    `else: rest = tokens[2:]`。第一行被两分支无条件覆盖，是死赋值；读代码的人会
    以为「默认 tokens[1:]」，实际永远走不到。

    **这条判据本轮抓到了真问题**：第一版修改后 `rest = tokens[...]` 出现 2 次 ——
    `console()` 与 `console_triggers_self_interrupt` 各抄一遍，导致谓词只看命令名
    不看选项，`reload --confirm xxx` 被误判「会中断」（假中断标记）。现已抽成
    `_parse_command_tokens` 单一入口。所以这里不只数赋值次数，还要断言
    「切 rest + 解析选项」只存在于那一个函数里。
    """
    source = _console_tool_source()
    assignments = _re.findall(r"^\s*rest = tokens\[", source, flags=_re.MULTILINE)
    assert len(assignments) == 1, f"`rest = tokens[...]` 必须只出现 1 次（当前 {len(assignments)} 次）"

    # 解析入口唯一：`cp._parse_options(` 只许在 `_parse_command_tokens` 内出现。
    parse_sites = [m.start() for m in _re.finditer(r"cp\._parse_options\(", source)]
    assert len(parse_sites) == 1, f"`cp._parse_options` 必须只有一个调用点（当前 {len(parse_sites)} 处）"
    owner_start = source.index("def _parse_command_tokens(")
    owner_end = source.index("\ndef ", owner_start)
    assert owner_start < parse_sites[0] < owner_end, (
        "`cp._parse_options` 的唯一调用点必须在 `_parse_command_tokens` 内——"
        "否则谓词与真执行又会各写一遍，重新长出「假中断标记」那个坑"
    )


def test_p165_c_destructive_commands_resolve_target_once():
    """删除命令的目标**只在命令表声明一次**，代码里不得再自行解析（#165 C）。

    旧实现的病：`_route` 里先 `_resolve_*` 拿 id/row（拼转发参数），`_handle_rm` 内的
    planner 又 resolve 一遍（拼 impact 文本）—— 同一请求查两次库，两次结果若在中途
    被改，行为无法预测。

    P2 的修法不是「把两次合并成一次」，而是**让它结构上不可能发生**：删掉了全部
    `_plan_*` planner，目标定位统一由 `CommandSpec.locator` 声明、由 `_locator_args`
    一处翻译成工具参数。所以判据也跟着换 —— 继续数 `_resolve_*` 的次数会变成
    「测一个已被删除的结构」（本单实测：planner 删掉后这条直接 ValueError 崩在
    `source.index`）。

    新判据（三条，任一条失效都说明定位又长出了第二个来源）：
      ① 源码里不存在任何 `_plan_<资源>_rm`（planner 不许复活）；
      ② 三条 delete 命令在命令表里都有 `locator`，且 `verb == "DELETE"`；
      ③ delete 的处理器（`verb=DELETE` 映射到的那个）体内不得出现 `cp._resolve_`。
    """
    import ast as _ast
    from service.funcToolService import consoleCommands as _cc

    source = _console_tool_source()

    # ① planner 不许复活：它的存在本身就是「同一件事算两遍」的载体。
    resurrected = _re.findall(r"def _plan_\w+\(", source)
    assert not resurrected, f"planner 已被删除，不得复活：{resurrected}"

    # ② 命令表必须声明定位形态（否则 `_locator_args` 静默返回 {}，命令会漏定位参数）。
    for key in ("dept rm", "room rm", "role rm"):
        spec = _cc.COMMAND_INDEX[key]
        assert spec.verb == "DELETE", f"{key} 的 verb 必须是 DELETE，当前 {spec.verb!r}"
        assert spec.locator is not None, f"{key} 必须在命令表里声明 locator"

    # ③ DELETE 处理器体内不得自行查库定位：定位只经 `_locator_args`。
    tree = _ast.parse(source)
    handler = _console_tool_module._VERB_HANDLERS["DELETE"]
    node = next(
        n for n in _ast.walk(tree)
        if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef)) and n.name == handler.__name__
    )
    body = _ast.unparse(node)
    resolves = _re.findall(r"_resolve_\w+", body)
    assert not resolves, (
        f"`{handler}` 体内出现了 {resolves}：目标定位必须只经 `_locator_args` 一处，"
        "否则同一个目标会有两个解析来源"
    )
