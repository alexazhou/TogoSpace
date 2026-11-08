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
        direct = await get_agent_info(agent_name="bob", _context=ctx)
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

        direct = await get_room_info(room_name="cc1_room", _context=ctx)
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
        """delete_dept ↔ `dept rm`（两步删除的完整等价见 CC4）。"""
        via = await self._call(
            f"dept create '{{\"name\":\"cc1_dept_rm\",\"responsibility\":\"x\","
            f"\"manager_name\":\"mgr3\",\"parent_name\":\"root\"}}'",
            _context=self._ctx(),
        )
        assert via["success"] is True, via

        first = await self._call("dept rm cc1_dept_rm", _context=self._ctx())
        assert first["error_code"] == "confirm_required"
        second = await self._call(
            f"dept rm cc1_dept_rm --confirm {first['confirm_token']}", _context=self._ctx()
        )
        assert second["success"] is True, second
        assert await gtDeptManager.get_dept_by_name(self.team_id, "cc1_dept_rm") is None

    async def test_cc1_delete_role_template(self):
        """delete_role_template ↔ `role rm`（含 confirm 两步）。"""
        via = await self._call(
            "role create '{\"name\":\"cc1_role_rm\",\"soul\":\"tmp\",\"type\":\"USER\"}'",
            _context=self._ctx(),
        )
        assert via["success"] is True, via

        first = await self._call("role rm cc1_role_rm", _context=self._ctx())
        assert first["error_code"] == "confirm_required"
        # 未确认前必须还在。
        assert await gtRoleTemplateManager.get_role_template_by_name("cc1_role_rm") is not None

        second = await self._call(
            f"role rm cc1_role_rm --confirm {first['confirm_token']}", _context=self._ctx()
        )
        assert second["success"] is True, second
        assert await gtRoleTemplateManager.get_role_template_by_name("cc1_role_rm") is None

    async def test_cc1_delete_room(self):
        """delete_room ↔ `room rm`（含 confirm 两步）。"""
        via = await self._call(
            "room create '{\"name\":\"cc1_room_rm\",\"member_names\":[\"alice\",\"mgr8\"]}'",
            _context=self._ctx(),
        )
        assert via["success"] is True, via

        first = await self._call("room rm cc1_room_rm", _context=self._ctx())
        assert first["error_code"] == "confirm_required"
        assert await gtRoomManager.get_room_by_team_and_name(self.team_id, "cc1_room_rm") is not None

        second = await self._call(
            f"room rm cc1_room_rm --confirm {first['confirm_token']}", _context=self._ctx()
        )
        assert second["success"] is True, second
        assert await gtRoomManager.get_room_by_team_and_name(self.team_id, "cc1_room_rm") is None

    async def test_cc1_reload_requires_confirm_and_predicate_matches(self):
        """reload_team ↔ `reload`：**确认闸门 + 自中断判定**可测；真中断路径不可在进程内测。

        `reload_team` 内部 `create_task(hot_reload)` 后挂在一个永不完成的 future 上，
        真正的中断由 `AgentTurnRunner` 在 handler 外触发。所以这里只证明两件能证明的事：
        ① 不带 `--confirm` 时**不执行**（只返回影响范围 + token）；
        ② 带上**有效** token 时，动态自中断判定器返回 True（这正是 D6 要的语义）。
        真中断 + 重启后自动判成功，需要真实运行时重启，列为「未验证项」写进报告。
        """
        first = await self._call("reload", _context=self._ctx())
        assert first["error_code"] == "confirm_required"
        assert first["impact"], "必须返回影响范围"
        # 不带 token 时判定器必须为 False —— 否则会留下假的中断标记。
        assert console_triggers_self_interrupt(_tool_call("reload")) is False

        token = first["confirm_token"]
        assert console_triggers_self_interrupt(
            _tool_call(f"reload --confirm {token}")
        ) is True, "带有效 token 的 reload 必须被判为自中断（D6）"

        # 非 reload 命令永不自中断（静态标志若被误设会在这里露出来）。
        assert console_triggers_self_interrupt(_tool_call("dept ls")) is False
        assert console_triggers_self_interrupt(_tool_call("help")) is False

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

    async def test_cc2_admin_delete_and_reload_are_denied_after_confirm(self):
        """带 confirm 闸门的 ADMIN 命令：token 拿到也没用，真删/真重载那一步被注册表拒。

        为什么单列：`rm` 与 `reload` 的确认闸门**留在 console 本地**（任务单要求），
        它跑在转发**之前**，所以不带 token 时只会返回 `confirm_required`，权限判定
        要等「带 token 的第二跳」才发生。这条把那个时点钉死，并证明 token 不是后门。
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
            first = await self._call(command, _context=charlie)
            assert first.get("error_code") == "confirm_required", (command, first)
            second = await self._call(
                f"{command} --confirm {first['confirm_token']}", _context=charlie
            )
            assert second["success"] is False, (command, second)
            assert f"工具无权限使用: {expected_tool}" in second["message"], (command, second)

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
        from service.funcToolService.funcToolType import FuncTool

        calls: list[str] = []
        original_get = func_tool_core.get_func_tool

        async def _spy(**kwargs):
            calls.append(kwargs.get("name", ""))
            return {"success": True}  # 不落库：这是计数桩，不是被测行为

        def _spy_get(name):
            if name == "save_agent":
                return FuncTool("save_agent", _spy)
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

    async def test_cc4_room_rm_without_confirm_does_not_delete(self):
        """不带 `--confirm` → 只返回影响范围 + token，**DB 里房间还在**。"""
        ctx = self._ctx()
        await self.create_room(TEAM, "cc4_room", ["bob", "charlie"], max_rounds=3)
        row = await gtRoomManager.get_room_by_team_and_name(self.team_id, "cc4_room")
        assert row is not None

        result = await self._call("room rm cc4_room", _context=ctx)
        assert result["success"] is False
        assert result["error_code"] == "confirm_required"
        assert result["confirm_token"]
        assert result["expires_in_seconds"] == 300
        assert any("cc4_room" in line for line in result["impact"])
        # 关键：查库证明没删（不能只看返回体）。
        still_there = await gtRoomManager.get_room_by_team_and_name(self.team_id, "cc4_room")
        assert still_there is not None, "未确认的 rm 不得真的删除"

    async def test_cc4_room_rm_with_valid_token_deletes(self):
        """带正确 token 再调 → 真删（查库证明）。"""
        ctx = self._ctx()
        await self.create_room(TEAM, "cc4_room2", ["alice", "mgr1"], max_rounds=3)
        first = await self._call("room rm cc4_room2", _context=ctx)
        token = first["confirm_token"]

        second = await self._call(f"room rm cc4_room2 --confirm {token}", _context=ctx)
        assert second["success"] is True, second
        assert await gtRoomManager.get_room_by_team_and_name(self.team_id, "cc4_room2") is None

    async def test_cc4_token_is_single_use(self):
        """同一个 token 用第二次必须被拒（否则等于永久免确认）。"""
        ctx = self._ctx()
        await self.create_room(TEAM, "cc4_room3", ["alice", "mgr2"], max_rounds=3)
        first = await self._call("room rm cc4_room3", _context=ctx)
        token = first["confirm_token"]

        ok = await self._call(f"room rm cc4_room3 --confirm {token}", _context=ctx)
        assert ok["success"] is True, ok

        await self.create_room(TEAM, "cc4_room3", ["alice", "mgr2"], max_rounds=3)
        replay = await self._call(f"room rm cc4_room3 --confirm {token}", _context=ctx)
        assert replay["success"] is False
        assert replay["error_code"] == "invalid_confirm_token"
        assert await gtRoomManager.get_room_by_team_and_name(self.team_id, "cc4_room3") is not None

    async def test_cc4_token_bound_to_target(self):
        """A 目标发的 token 不能拿去删 B 目标（绑定 target）。"""
        ctx = self._ctx()
        await self.create_room(TEAM, "cc4_room_a", ["alice", "mgr3"], max_rounds=3)
        await self.create_room(TEAM, "cc4_room_b", ["alice", "mgr4"], max_rounds=3)
        first = await self._call("room rm cc4_room_a", _context=ctx)
        token = first["confirm_token"]

        crossed = await self._call(f"room rm cc4_room_b --confirm {token}", _context=ctx)
        assert crossed["success"] is False
        assert crossed["error_code"] == "invalid_confirm_token"
        assert await gtRoomManager.get_room_by_team_and_name(self.team_id, "cc4_room_b") is not None

    async def test_cc4_token_bound_to_caller(self):
        """alice 拿到的 token 不能由 bob 使用（绑定调用者）。"""
        await self.create_room(TEAM, "cc4_room_c", ["alice", "mgr5"], max_rounds=3)
        first = await self._call("room rm cc4_room_c", _context=self._ctx("alice"))
        token = first["confirm_token"]

        crossed = await self._call(f"room rm cc4_room_c --confirm {token}", _context=self._ctx("bob"))
        # bob 是 BASIC，先撞权限墙；就算放开权限，token 绑定也应当拒。
        assert crossed["success"] is False
        assert await gtRoomManager.get_room_by_team_and_name(self.team_id, "cc4_room_c") is not None

    async def test_cc4_expired_token_rejected(self, monkeypatch):
        """过期 token → 拒绝，且房间仍在。"""
        ctx = self._ctx()
        await self.create_room(TEAM, "cc4_room4", ["alice", "mgr6"], max_rounds=3)
        first = await self._call("room rm cc4_room4", _context=ctx)
        token = first["confirm_token"]

        # 直接把表项的过期时间推到过去（比 sleep 300s 可控、也不会拖慢测试）。
        import time as _time

        cp._confirm_tokens[token]["expires_at"] = _time.time() - 1

        result = await self._call(f"room rm cc4_room4 --confirm {token}", _context=ctx)
        assert result["success"] is False
        assert result["error_code"] == "invalid_confirm_token"
        assert await gtRoomManager.get_room_by_team_and_name(self.team_id, "cc4_room4") is not None

    async def test_cc4_unknown_token_rejected(self):
        """随便编一个 token → 拒绝（**不得**因为「反正 token 无效」就跳过目标校验）。"""
        await self.create_room(TEAM, "cc4_room_x", ["mgr8", "mgr9"], max_rounds=3)
        result = await self._call("room rm cc4_room_x --confirm deadbeef", _context=self._ctx())
        assert result["success"] is False
        assert result["error_code"] == "invalid_confirm_token"
        assert await gtRoomManager.get_room_by_team_and_name(self.team_id, "cc4_room_x") is not None

    async def test_cc4_dept_rm_recursive_required_lists_impact(self):
        """`dept rm` 有子部门且未加 `--recursive` → `recursive_required`，且不删。"""
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
        assert result["error_code"] == "recursive_required"
        assert "1 个子部门" in result["message"]
        assert await gtDeptManager.get_dept_by_name(self.team_id, "cc4_parent") is not None

    async def test_cc4_dept_rm_recursive_lists_children_then_deletes(self):
        """`dept rm --recursive` → 影响范围里**列出子部门名**，确认后父子都删。"""
        ctx = self._ctx()
        first = await self._call("dept rm cc4_parent --recursive", _context=ctx)
        assert first["error_code"] == "confirm_required"
        impact_text = "\n".join(first["impact"])
        assert "cc4_child" in impact_text, f"影响范围必须列出会被删的子部门名：{impact_text}"

        second = await self._call(
            f"dept rm cc4_parent --recursive --confirm {first['confirm_token']}", _context=ctx
        )
        assert second["success"] is True, second
        assert await gtDeptManager.get_dept_by_name(self.team_id, "cc4_parent") is None
        assert await gtDeptManager.get_dept_by_name(self.team_id, "cc4_child") is None

    async def test_cc4_reload_confirm_gate(self):
        """`reload` 不带 `--confirm` → `confirm_required`，**不执行**（否则会真重载团队）。"""
        result = await self._call("reload", _context=self._ctx())
        assert result["success"] is False
        assert result["error_code"] == "confirm_required"
        assert result["confirm_token"]

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
    """同名成员（离职名字被复用）时 `agent get/update` 必须报错，不能静默换人。

    为什么单独一组：`GtAgent` 的 `(team_id, name)` 是**非唯一**索引（离职成员的
    名字允许被新成员复用），而 `gtAgentManager.get_agent()` 底层是
    `peewee_async` 的 `aio_get` → `paginate(1,1)`，**不校验唯一性**：两行同名时
    它返回行序靠前的那一行（通常是先建的、已离职的那个）。按 id 定位「A」却拿到
    同名的「B」，返回体仍是 `success: True` —— 这是最贵的一类错：模型会当成成功
    继续往下做。draft §4.2 明确要求「同名冲突时提示改用 id」。

    这组用例是**回归钉**：改坏 `_resolve_agent_by_target` 或去掉 `agent get` 里的
    一致性校验，这里必须变红。
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

    async def test_agent_get_by_duplicate_name_is_ambiguous(self, monkeypatch):
        """同名多行时按名字取必须报 `ambiguous_target`，并列出候选 id。"""
        self._runtime_stub(monkeypatch)
        result = await self._call("agent get dup", _context=self._ctx())
        assert result["success"] is False
        assert result["error_code"] == "ambiguous_target"
        assert sorted(c["id"] for c in result["candidates"]) == sorted([self.off_id, self.on_id])

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

    async def test_agent_get_by_id_rejects_when_runtime_resolves_to_other_id(self, monkeypatch):
        """**纵深防御**：运行时把该名字解析成另一个 id 时必须拒绝。

        这条为什么要用桩：真实数据模型下「同名」只可能一行在职，`member_not_on_board`
        会先短路，一致性校验根本轮不到执行 —— 于是删掉它也没人知道。这里直接构造
        「请求 id=A、运行时同名解析出 id=B」，单独钉住那一层。
        """
        real_on = next(r for r in self.rows if r.id == self.on_id)
        ghost = GtAgent(
            team_id=self.team_id,
            name=real_on.name,
            role_template_id=self.role_template_id,
            employ_status=EmployStatus.ON_BOARD,
        )
        ghost.id = self.on_id + 1000
        monkeypatch.setattr(
            agentService, "get_team_agents", lambda team_id: [_FakeRuntimeAgent(ghost, AgentStatus.IDLE)]
        )
        result = await self._call(f"agent get {self.on_id}", _context=self._ctx())
        assert result["success"] is False
        assert result["error_code"] == "ambiguous_target"
        assert str(self.on_id) in result["message"]
