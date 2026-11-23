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
    create_dept,
    create_room,
    update_agent,
    update_dept,
    update_room,
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
        """`dept get <id>` 与 `get_dept_info(dept_id=…)` 等价；按名称被拒并提示先 `ls`。

        P0 变化 b：`dept get` 的位置参数是**必填正整数 ID**，按名查询通道取消。
        所以这条从「名字与 ID 两条通道等价」改成「ID 通道等价 + 名字通道明确报错」——
        名字通道如果被悄悄加回来，这里会红。
        """
        root = await gtDeptManager.get_dept_by_name(self.team_id, "root")
        assert root is not None

        direct = await get_dept_info(dept_id=root.id, _context=self._ctx())
        by_id = await self._call(f"dept get {root.id}", _context=self._ctx())
        by_name = await self._call("dept get root", _context=self._ctx())

        assert by_id["success"] is True, by_id
        assert _as_tool_result(direct)["dept"] == by_id["dept"]
        assert by_name["success"] is False, by_name
        assert by_name["error_code"] == "invalid_target", by_name
        assert "dept ls" in by_name["message"], by_name["message"]

    async def test_cc1_create_dept(self):
        """`create_dept`(新建) ↔ `dept create <json>`（P0 前是 `save_dept(overwrite=False)`）。"""
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
        direct = await create_dept(
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

    async def test_cc1_update_dept_is_patch(self):
        """`update_dept`(更新) ↔ `dept update <id>`，且是 PATCH（见 CC5）。

        P0 起 `dept update` 的位置参数是**必填正整数 ID**（变化 b）：按名更新通道已取消，
        名称要先 `dept ls` 换成 ID。这里正是「按 ID 更新」的真入口验证。
        """
        row = await gtDeptManager.get_dept_by_name(self.team_id, "cc1_dept")
        assert row is not None
        via = await self._call(
            f"dept update {row.id} '{{\"responsibility\":\"改过的职责\"}}'", _context=self._ctx()
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
        """`agent get <id>` 与工具同名参数等价；按名称被拒并提示先 `agent ls`（变化 b）。"""
        runtimes = await self._fake_runtime_agents()
        monkeypatch.setattr(agentService, "get_team_agents", lambda team_id: runtimes)
        ctx = self._ctx()
        direct = await get_agent_info(agent_id=self.agent_ids["bob"], _context=ctx)
        by_id = await self._call(f"agent get {self.agent_ids['bob']}", _context=ctx)
        by_name = await self._call("agent get bob", _context=ctx)
        assert by_id["success"] is True, by_id
        assert by_id["agent"] == _as_tool_result(direct)["agent"], "`agent get <id>` 必须按 id 定位到同一个成员"
        assert by_name["success"] is False, by_name
        assert by_name["error_code"] == "invalid_target", by_name
        assert "agent ls" in by_name["message"], by_name["message"]

    async def test_cc1_create_agent(self):
        """`create_agent`(新建) ↔ `agent create <json>`。"""
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

    async def test_cc1_update_agent_is_patch(self):
        """`update_agent`(更新) ↔ `agent update <id>`，且未出现的字段保持原值。"""
        before = await gtAgentManager.get_agent(self.team_id, "cc1_agent", status=None)
        assert before is not None
        via = await self._call(
            f"agent update {before.id} '{{\"model\":\"gpt-4o-mini\"}}'", _context=self._ctx()
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

        direct = await get_room_info(room_id=row.id, _context=ctx)
        by_id = await self._call(f"room get {row.id}", _context=ctx)
        by_name = await self._call("room get cc1_room", _context=ctx)
        assert by_id["room"] == _as_tool_result(direct)["room"], "`room get <id>` 必须按 id 定位到同一个房间"
        assert by_name["success"] is False and by_name["error_code"] == "invalid_target", by_name
        assert "room ls" in by_name["message"], by_name["message"]

        direct_ls = await get_room_info(_context=ctx)
        via_ls = await self._call("room ls", _context=ctx)
        assert [r["room_name"] for r in via_ls["rooms"]] == [
            r["room_name"] for r in direct_ls["rooms"]
        ]

    async def test_cc1_room_ls_to_get_closure(self):
        """RC-3 闭环（#335 / C-02）：`room ls` → 取 `room_id` → `room get <id>` 必须走通。

        这一条打的是**用户流程**而不是字段存在性：列表项里只允许用 `room_id` 这一项
        去驱动下一步（不看 `room_name`、不借夹具已知的 row.id），走得通才算闭环。
        顺便把两端的返回体原文打出来，报告里的黑盒证据就来自这里（`pytest -s` 采集）。

        为什么**不新建房间**：产品有「成员集合相同的房间不能重复创建」这条规则
        （`ROOM_DUPLICATE`），本文件后面的 `test_cc1_create_room` 会用 alice+bob 建
        `cc1_room2`。这里若自建一个 alice+bob 的房间，就会把后面那条用例弄红——
        首版就是这样踩到的（实测报 `已存在成员相同的房间 'cc1_closure_room'`）。
        改用类级 setup 里必然存在的部门房间 `root`，既零副作用，也不依赖用例顺序。
        """
        ctx = self._ctx()

        listed = await self._call("room ls", _context=ctx)
        print("\n[black-box] room ls =>", json.dumps(listed, ensure_ascii=False))
        assert listed["success"] is True, listed
        target = next(r for r in listed["rooms"] if r["room_name"] == "root")
        # 断链的形态就是这里 KeyError；显式断言让失败信息可读。
        assert "room_id" in target, f"列表项必须带 room_id，实际键={sorted(target)}"
        room_id = target["room_id"]

        detail = await self._call(f"room get {room_id}", _context=ctx)
        print(f"[black-box] room get {room_id} =>", json.dumps(detail, ensure_ascii=False))
        assert detail["success"] is True, detail
        # 顶层键契约：ls 仍是 rooms（数组）、get 仍是 room（单数对象）。
        assert set(listed) == {"success", "rooms"}
        assert set(detail) == {"success", "room"}
        # 详情项也带 room_id，且就是列表里拿到的那个 ID。
        assert detail["room"]["room_id"] == room_id
        assert detail["room"]["room_name"] == "root"

    async def test_cc1_create_room(self):
        """`create_room`(新建) ↔ `room create <json>`。"""
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

    async def test_cc1_update_room_is_patch(self):
        """`update_room`(更新) ↔ `room update <id>`，且是 PATCH。"""
        before = await gtRoomManager.get_room_by_team_and_name(self.team_id, "cc1_room2")
        assert before is not None
        via = await self._call(
            f"room update {before.id} '{{\"max_rounds\":12}}'", _context=self._ctx()
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

    async def test_cc1_role_template_create_and_update(self):
        """`create_role_template` / `update_role_template` ↔ `role create` / `role update`（update 是 PATCH）。"""
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

        # 变化 e：按名删除改成 `--name`（位置参数只剩可选的正整数 ID）。
        result = await self._call("dept rm --name cc1_dept_rm", _context=self._ctx())
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

        result = await self._call("room rm --name cc1_room_rm", _context=self._ctx())
        assert result["success"] is True, result
        assert any(entry["name"] == "cc1_room_rm" for entry in result["deleted"]), result
        assert await gtRoomManager.get_room_by_team_and_name(self.team_id, "cc1_room_rm") is None

    async def test_cc1_forward_target_executes_exactly_once(self, monkeypatch):
        """`task ls` 走完整链路（console → 注册表第 2 跳）：目标工具**只执行 1 次**。

        这条是「删除 `TestHandleListForwardsExactlyOnce`」的替代物，粒度说清楚：
        - 原判据把 `_forward` 换成「返回而不抛」的 stub 数**抛出次数**，钉的是 console
          内部的 if/else 分支不重复调用；
        - `_handle_list` 随 P0 删除后，模块内 `ForwardToolCall(` 只剩**一个**构造点
          （`_dispatch`），由结构判据 `test_p165_b_...` 咬住「双抛」这一类失效；
        - 行为面还剩一个真实症状要钉：**目标工具被执行两次**（重复执行会写两次库）。
          这里用「可观察的外部计数」——注册表第 2 跳实际调到目标函数的次数 == 1。

        计数桩只替换目标函数本体，`get_func_tool` 返回的 `FuncTool` 保留真工具的
        基准可见性（`BOTH`），否则命令会在解析期被判为不可用，计数恒 0 变成假判据。
        """
        from constants import ToolVisibility
        from service.funcToolService.funcToolType import FuncTool

        calls: list[dict] = []
        original_get = func_tool_core.get_func_tool

        def _spy(**kwargs):
            calls.append(kwargs)
            return {"success": True, "tasks": [], "count": 0, "total": 0}

        def _spy_get(name):
            if name == "list_tasks":
                return FuncTool("list_tasks", _spy, visibility=ToolVisibility.BOTH)
            return original_get(name)

        monkeypatch.setattr(func_tool_core, "get_func_tool", _spy_get)

        result = await self._call("task ls --status TODO", _context=self._ctx())

        assert result["success"] is True, result
        assert len(calls) == 1, f"目标工具被执行了 {len(calls)} 次（双转发会写两次库）：{calls}"
        # 转发参数只有调用方给的过滤项（补参已归工具层，见单测 TestTaskLsForwardsRaw）。
        assert calls[0].get("status") == ["TODO"], calls[0]
        assert "scope" not in calls[0], calls[0]

        # 量具自证（判据九问第 1/8 条）：计数器必须随调用次数增长，
        # 否则「== 1」可能只是「列表永远只收一条」的假绿。
        _spy()
        assert len(calls) == 2, "计数器不自增 ⇒ 上面的 == 1 没有判别力"

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
        assert "工具无权限使用: create_agent" in result["message"], result
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
            ("role create '{\"name\":\"x\",\"soul\":\"y\"}'", "create_role_template"),
            # `list_role_templates` 在 CATEGORY_CONFIG 里就是 ADMIN（不是 BASIC），
            # 所以 `role ls` 必须按 ADMIN 校验——这条正是「按等价原工具查表」的体现。
            ("role ls", "list_role_templates"),
            # update 类命令的转发目标是 update_*，且位置参数必须是 ID（P0 变化 a/b）。
            ("agent update 1 '{\"model\":\"m\"}'", "update_agent"),
            ("room update 1 '{\"max_rounds\":3}'", "update_room"),
        ],
    )
    async def test_cc2_every_admin_subcommand_is_denied_for_basic_caller(
        self, command, expected_tool
    ):
        """**逐条**验证管理类子命令对 BASIC 调用者都关门（不是只测 create_agent）。

        单一用例容易漏掉「加新命令时没登记等价工具」——这里按命令表遍历，
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
            # 变化 e：按名删除走 `--name`（位置参数只剩可选的正整数 ID）。
            ("dept rm --name cc2_rm_dept", "delete_dept"),
            ("room rm --name cc2_rm_room", "delete_room"),
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

    async def test_cc2_fail_closed_when_tool_is_not_declared(self, monkeypatch):
        """工具没声明命令时必须 **拒绝**，不能「没声明就放行」（D3 fail-closed）。

        P0 之后「命令 ↔ 工具」的关系是**派生**的：命令只存在于「工具的 `@tool` 声明」里。
        因此漏声明的表现不是「转发目标为空」，而是**这条命令在命令表里根本不存在** ——
        `resolve` 直接失败，console 报 `unknown_command`，绝不会退化成「执行 console 自己」。
        这条判据把这个结构性保证钉死：手工构造一张**少了 `create_agent`** 的表，
        确认 `agent create` 变成未知资源，同时同一张表上 `dept ls` 仍然正常（对照，
        证明不是「整张表都不可用」造成的假绿）。
        """
        from service.funcToolService import consoleRegistry as _registry
        from service.funcToolService import toolSpec as _toolSpec
        from service.funcToolService.funcToolType import FuncTool

        partial = [
            FuncTool(
                name=decl.name,
                callable=decl.func,
                visibility=decl.spec.visibility,
                spec=decl.spec,
            )
            for decl in _toolSpec.declared_tools()
            if decl.name != "create_agent"
        ]
        table = _registry.build(partial)
        monkeypatch.setattr(_registry, "get_table", lambda: table)

        result = await self._call(
            "agent create '{\"name\":\"cc2_failopen\",\"role_template_name\":\"test_role\"}'",
            _context=self._ctx("alice"),  # 连根主管也拒：这是结构问题，不是权限问题
        )
        assert result["success"] is False, result
        assert result["error_code"] == "unknown_command", result
        assert await gtAgentManager.get_agent(self.team_id, "cc2_failopen", status=None) is None

        # 对照：同一张表上别的命令仍可用 ⇒ 上面的拒绝确实来自「命令不存在」。
        control = await self._call("dept ls", _context=self._ctx("alice"))
        assert control["success"] is True, control

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
            if name == "create_agent":
                # 桩必须保留真工具的基准可见性：console 路由按 `console_visible`
                # 过滤命令，默认 TOOL_ONLY 会让命令在解析期就被判为不可用。
                return FuncTool("create_agent", _spy, visibility=ToolVisibility.BOTH)
            return original_get(name)

        monkeypatch.setattr(func_tool_core, "get_func_tool", _spy_get)

        denied = await self._call(
            "agent create '{\"name\":\"cc2_spy\",\"role_template_name\":\"test_role\"}'",
            _context=self._ctx("bob"),
        )
        assert "工具无权限使用: create_agent" in denied["message"], denied
        assert calls == [], f"被拒的命令不得触达 create_agent，实际调用：{calls}"

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
        """create 什么都不给 → `missing_required_option`（必填项前置到签名，设计 §2.2）。

        改前必填项在 schema 里是「可选」，只能靠「既无内联 JSON 又无 payload」反推意图，
        报 `missing_input_source`。P0 把必填项放回签名后，检查点前移到**必填校验**，
        所以错误码变成 `missing_required_option`，并且能直接点名缺哪几个字段 ——
        比「你没给输入」更能让模型自纠。矩阵里没有 `missing_input_source` 了。
        """
        result = await self._call("agent create", _context=self._ctx())
        assert result["success"] is False, result
        assert result["error_code"] == "missing_required_option", result
        assert set(result["missing"]) == {"name", "role_template_name"}, result

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

        result = await self._call("room rm --name cc4_room", _context=ctx)
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
        result = await self._call("room rm --name cc4_no_such_room", _context=self._ctx())
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
        result = await self._call("room rm --name root", _context=ctx)
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
        result = await self._call("dept rm --name cc4_parent", _context=ctx)
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
        result = await self._call("dept rm --name cc4r_parent --recursive", _context=ctx)
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
            f"dept update {before.id} '{{\"manager_name\":\"charlie\"}}'", _context=ctx
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

        arr_row = await gtDeptManager.get_dept_by_name(self.team_id, "cc5_dept_arr")
        assert arr_row is not None
        result = await self._call(
            f"dept update {arr_row.id} '{{\"member_names\":[\"mgr9\"]}}'", _context=ctx
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

        result = await self._call(f"agent update {before.id} '{{\"model\":\"m2\"}}'", _context=ctx)
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

        result = await self._call(f"room update {before.id} '{{\"max_rounds\":9}}'", _context=ctx)
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
        row = await gtDeptManager.get_dept_by_name(self.team_id, "cc6_dept")
        assert row is not None
        result = await self._call(
            f"dept update {row.id} --responsibility 'cc6 改过'", _context=self._ctx()
        )
        assert result["success"] is True, result
        saved = await gtDeptManager.get_dept_by_name(self.team_id, "cc6_dept")
        assert saved is not None
        assert saved.responsibility == "cc6 改过"

    async def test_cc6_same_field_in_option_and_json_is_ambiguous(self):
        """同一字段同时出现在 `--选项` 与内联 JSON → ambiguous_input_source，且不写库。"""
        row = await gtDeptManager.get_dept_by_name(self.team_id, "cc6_dept")
        assert row is not None
        result = await self._call(
            f"dept update {row.id} --responsibility 'a' '{{\"responsibility\":\"b\"}}'",
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
        """定位参数从 `--选项` 再给一次（与位置槽来源重复）→ ambiguous_input_source。"""
        root = await gtDeptManager.get_dept_by_name(self.team_id, "root")
        assert root is not None
        result = await self._call(
            f"dept update {root.id} --dept_id {root.id + 1}", _context=self._ctx()
        )
        assert result["success"] is False
        assert result["error_code"] == "ambiguous_input_source"

    async def test_cc6_update_with_only_mechanism_option_is_no_update_action(self):
        """机制参数已被删除：`--overwrite` 现在是 `unknown_option`（变化 a）。

        改前 `--overwrite` 是 `save_*` 的机制开关，只给机制不给字段 ⇒ `no_update_action`。
        P0 拆分后这个开关不存在，同一句命令停在选项层报 `unknown_option`。
        「无有效变更」语义本身仍在，由下面 `test_cc6_blank_config_counts_as_no_update_action`
        （空白 JSON）钉住 —— 所以这里换的是**载体**，不是放弃判据。
        """
        row = await gtDeptManager.get_dept_by_name(self.team_id, "cc6_dept")
        assert row is not None
        result = await self._call(
            f"dept update {row.id} --overwrite", _context=self._ctx()
        )
        assert result["success"] is False
        assert result["error_code"] == "unknown_option", result

    async def test_cc6_blank_config_counts_as_no_update_action(self):
        """配置里只有空白值 = 没有可执行动作 → no_update_action（不静默成功）。"""
        row = await gtDeptManager.get_dept_by_name(self.team_id, "cc6_dept")
        assert row is not None
        result = await self._call(
            f"dept update {row.id} '{{\"responsibility\":\"   \"}}'", _context=self._ctx()
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

    这组用例是**回归钉**：改坏「更新必须按 ID 定位」的这道闸（原在
    `consoleParsing._resolve_agent_by_target` 的同名消歧，P0 起由位置槽的必填 int
    直接挡住），这里必须变红。

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
    同名保护只留在 `update_agent` 上——而且它现在连按名通道都没有，只能按 `agent_id`。
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

    async def test_agent_get_by_duplicate_name_is_rejected_without_ambiguity(self, monkeypatch):
        """`agent get <同名>` 被位置槽层直接拒绝：歧义问题在**入口**就消失（变化 b）。

        P2 的语义是「console 纯转发，由原工具取运行时第一个同名行」——也就是允许
        「静默取错人」。P0 把 `agent get` 的位置参数改成必填正整数 ID 后，按名查询
        这条通道没有了：同名多行根本走不到原工具，`invalid_target` 就停在命令层并提示
        先 `agent ls` 取 ID。判据跟着改成「按名必须被拒 + 按 ID 取到指定那行」，
        哪天有人把按名通道加回来（重新引入静默取错人的可能），这里会红。
        """
        self._runtime_stub(monkeypatch)
        by_name = await self._call("agent get dup", _context=self._ctx())
        assert by_name["success"] is False, by_name
        assert by_name["error_code"] == "invalid_target", by_name
        assert "agent ls" in by_name["message"], by_name["message"]

        by_id = await self._call(f"agent get {self.on_id}", _context=self._ctx())
        assert by_id["success"] is True, by_id
        assert by_id["agent"]["id"] == self.on_id

    async def test_agent_update_by_name_is_rejected_at_locator_layer(self):
        """按**名字**敲 `agent update` 必须被位置槽层拒绝，且库里一行都不能被改。

        P0 变化 b：`agent update` 的位置参数是必填正整数 ID（完整 ID 唯一避免同名歧义），
        按名更新通道取消。所以「同名多行」这件事现在更早被挡住——连 `ambiguous_target`
        都不用走到：`invalid_target` 就停在命令层，并提示先 `agent ls` 取 ID。

        改前判据钉的是 `ambiguous_target`（旧路径 `agent update dup` → 按名查全量 →
        多行同名报歧义）。那条路径已随「位置参数必须 ID」消失，继续断言它等于要求
        把按名更新通道加回来。
        """
        before = {r.id: r.model for r in await gtAgentManager.get_team_all_agents(self.team_id) if r.name == "dup"}
        result = await self._call("agent update dup '{\"model\":\"should-not-be-written\"}'", _context=self._ctx())
        assert result["success"] is False
        assert result["error_code"] == "invalid_target", result
        assert "agent ls" in result["message"], result["message"]
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
    """`ForwardToolCall` 在本模块只构造一次，且那次在 `_dispatch` 内（#165 B）。

    转发是**控制流信号**：多一个构造点就意味着有一条路径绕开命令表的 `tool_name`，
    自己拼目标名（P2 之前 `dept rm` / `room rm` / `role rm` 就各手写过一处字面量，
    于是「目标名只从命令表取」这条不变式是假的）。

    P0 之后构造点从 `_forward` 收拢到 `_dispatch`（`_forward` 系列处理器已删除），
    判据只换函数名，语义不变：**唯一构造点 + 必须在那个入口里**。
    """
    source = _console_tool_source()
    sites = [m for m in _re.finditer(r"ForwardToolCall\(", source)]
    assert len(sites) == 1, (
        f"`ForwardToolCall` 构造点必须唯一（当前 {len(sites)} 处）："
        f"{[source[max(0, m.start()-60):m.start()+20] for m in sites]}"
    )
    owner_start = source.index("def _dispatch(")
    owner_end = source.index("\ndef ", owner_start)
    assert owner_start < sites[0].start() < owner_end, "唯一的构造点必须在 `_dispatch` 内"


def test_p165_a_pipeline_has_single_implementation():
    """四步流水线在本模块只有**一份**实现，且谓词与真执行共用它（#165 A 的同型缺陷）。

    为什么这条最重要：`console_triggers_self_interrupt`（自中断谓词）必须回答「这条命令
    会不会真的走到转发」。若它自己再抄一遍解析链，一旦与真执行漂移，就会留下**假中断
    标记**——P2 之前实测踩过（`reload --confirm xxx` 被误判为会中断）。P0 重写时这一
    风险原样存在，所以把「流水线只有一份」做成结构判据：

      ① `cp._parse_options` 在本模块只出现 1 次；
      ② 那一次在 `_build_call_args` 内（唯一实现）；
      ③ `console_triggers_self_interrupt` 必须调用 `_build_call_args`（而不是自己拼）。
    """
    source = _console_tool_source()
    parse_sites = [m.start() for m in _re.finditer(r"cp\._parse_options\(", source)]
    assert len(parse_sites) == 1, f"`cp._parse_options` 必须只有一个调用点（当前 {len(parse_sites)} 处）"

    owner_start = source.index("def _build_call_args(")
    owner_end = source.index("\ndef ", owner_start)
    assert owner_start < parse_sites[0] < owner_end, (
        "`cp._parse_options` 的唯一调用点必须在 `_build_call_args` 内——否则谓词与真执行"
        "各写一遍，重新长出「假中断标记」那个坑"
    )

    predicate_start = source.index("def console_triggers_self_interrupt(")
    # 后面那个顶层函数是 `async def console(...)`，所以不能只找 "\ndef "。
    predicate_end = _re.search(r"\n(?:async\s+)?def ", source[predicate_start:]).start() + predicate_start
    assert "_build_call_args(" in source[predicate_start:predicate_end], (
        "自中断谓词必须复用 `_build_call_args`，不得自己拼解析链"
    )


def test_p165_c_destructive_commands_resolve_target_once():
    """删除命令的目标**只在命令表声明一次**，`consoleTool` 里不得再自行解析（#165 C）。

    旧实现的病：`_route` 里先 `_resolve_*` 拿 id/row（拼转发参数），`_handle_rm` 内的
    planner 又 resolve 一遍（拼 impact 文本）——同一请求查两次库。P0 的修法同样是
    「让它结构上不可能发生」：目标定位统一由 `ConsoleCommand.args` 的位置槽声明、
    由 `_resolve_positionals` 一处翻译成工具参数，`consoleTool` 不再碰任何 Manager。

    三条判据（任一条失效都说明定位又长出了第二个来源）：
      ① 源码里不存在任何 `_plan_<资源>_rm`（planner 不许复活）；
      ② `dept rm` / `room rm` / `role rm` 在命令表里都声明了位置槽参数（定位形态有据可查）；
      ③ `consoleTool` 源码里不出现任何 `Manager.` 查库调用（它必须保持「纯翻译」）。
    """
    from service.funcToolService import consoleRegistry as _registry

    source = _console_tool_source()

    # ① planner 不许复活：它的存在本身就是「同一件事算两遍」的载体。
    resurrected = _re.findall(r"def _plan_\w+\(", source)
    assert not resurrected, f"planner 已被删除，不得复活：{resurrected}"

    # ② 定位形态必须在命令表里声明（否则命令会漏定位参数）。
    table = _registry.get_table()
    for key in ("dept rm", "room rm", "role rm"):
        cmd = table.by_path[key]
        assert cmd.positional_args, f"{key} 必须在命令表里声明位置槽（当前没有）"

    # ③ 命令层不许自己查库定位：定位只经命令表 + `_resolve_positionals`。
    managers = _re.findall(r"\bgt\w*Manager\b", source)
    assert not managers, f"`consoleTool` 不得直接查库（发现 {managers}）"
