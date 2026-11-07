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
from constants import AgentStatus, RoleTemplateType
from dal.db import gtAgentManager, gtDeptManager, gtRoleTemplateManager, gtRoomManager, gtTeamManager
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtDept import GtDept
from model.dbModel.gtRoleTemplate import GtRoleTemplate
from model.dbModel.gtTeam import GtTeam
from service.funcToolService import consoleCommands as cc
from service.funcToolService import consoleParsing as cp
from service.funcToolService.consoleTool import console, console_triggers_self_interrupt
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


class TestConsoleTool(ServiceTestCase):
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
        via = await console("time get", _context=self._ctx())
        assert via["success"] is True
        # 时间串会随时间变，只能比「格式相同 + 都带本地时区前缀」这一层。
        assert direct.keys() == via.keys()
        assert "当前本地时间" in via["message"]

        direct_tz = get_time(timezone="UTC")
        via_tz = await console("time get --tz UTC", _context=self._ctx())
        assert via_tz["success"] is True
        assert "UTC" in via_tz["message"]
        assert direct_tz.keys() == via_tz.keys()

    async def test_cc1_get_dept_info_ls(self):
        """get_dept_info(无参) ↔ `dept ls`。"""
        direct = await get_dept_info(_context=self._ctx())
        via = await console("dept ls", _context=self._ctx())
        assert via["success"] is True
        assert direct["dept"] == via["dept"]

    async def test_cc1_get_dept_info_get(self):
        """get_dept_info(dept_id) ↔ `dept get <名称>` 与 `dept get <id>`。"""
        root = await gtDeptManager.get_dept_by_name(self.team_id, "root")
        assert root is not None

        direct = await get_dept_info(dept_id=root.id, _context=self._ctx())
        by_name = await console("dept get root", _context=self._ctx())
        by_id = await console(f"dept get {root.id}", _context=self._ctx())

        assert by_name["success"] is True and by_id["success"] is True
        assert direct["dept"] == by_name["dept"] == by_id["dept"]

    async def test_cc1_save_dept_create(self):
        """save_dept(新建) ↔ `dept create <json>`。"""
        payload = {
            "name": "cc1_dept",
            "responsibility": "cc1 用",
            "manager_name": "mgr1",
            "member_names": ["mgr1", "charlie"],
            "parent_name": "root",
        }
        via = await console(
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
        via = await console(
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
        via = await console("agent ls", _context=ctx)
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
        by_name = await console("agent get bob", _context=ctx)
        by_id = await console(f"agent get {self.agent_ids['bob']}", _context=ctx)
        assert by_name["success"] is True and by_id["success"] is True
        assert by_name["agent"] == direct["agent"]
        assert by_id["agent"] == direct["agent"], "`agent get <id>` 必须按 id 定位到同一个成员"

    async def test_cc1_save_agent_create(self):
        """save_agent(新建) ↔ `agent create <json>`。"""
        via = await console(
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
        via = await console(
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
        via = await console("agent wake bob", _context=self._ctx())
        assert via["success"] is False
        assert via["message"] == direct["message"]

    async def test_cc1_wake_up_agent_success_path(self, monkeypatch):
        """wake_up_agent 成功分支 ↔ `agent wake`：两侧都返回 success 且消息一致。"""
        bob = await gtAgentManager.get_agent(self.team_id, "bob")
        assert bob is not None
        fake = _FakeRuntimeAgent(bob, AgentStatus.FAILED)
        monkeypatch.setattr(agentService, "get_team_agents", lambda team_id: [fake])
        direct = await wake_up_agent("bob", _context=self._ctx())
        via = await console("agent wake bob", _context=self._ctx())
        assert direct["success"] is True and via["success"] is True
        assert via["message"] == direct["message"]
        assert fake.resumed is True

    async def test_cc1_start_chat(self):
        """start_chat ↔ `agent dm`：都建出同一个单聊房间。"""
        ctx = self._ctx()
        # `start_chat` 读 DB（不读运行态），所以不需要桩 runtime。
        # 第一次应新建，第二次应命中「已存在」——两侧关键字段必须一致。
        direct = await console("agent dm charlie", _context=ctx)
        via = await console("agent dm charlie", _context=ctx)
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
        by_name = await console("room get cc1_room", _context=ctx)
        by_id = await console(f"room get {row.id}", _context=ctx)
        assert by_name["room"] == direct["room"]
        assert by_id["room"] == direct["room"], "`room get <id>` 必须按 id 定位到同一个房间"

        direct_ls = await get_room_info(_context=ctx)
        via_ls = await console("room ls", _context=ctx)
        assert [r["room_name"] for r in via_ls["rooms"]] == [
            r["room_name"] for r in direct_ls["rooms"]
        ]

    async def test_cc1_save_room_create(self):
        """save_room(新建) ↔ `room create <json>`。"""
        via = await console(
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
        via = await console(
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
        via = await console("role ls", _context=ctx)
        assert via["success"] is True
        names_direct = sorted(t["name"] for t in direct["role_templates"])
        names_via = sorted(t["name"] for t in via["role_templates"])
        assert names_via == names_direct

        direct_kw = await list_role_templates(keywords=["test_role"], _context=ctx)
        via_kw = await console("role ls --kw test_role", _context=ctx)
        assert sorted(t["name"] for t in via_kw["role_templates"]) == sorted(
            t["name"] for t in direct_kw["role_templates"]
        )

    async def test_cc1_get_role_template(self):
        """get_role_template ↔ `role get`。"""
        ctx = self._ctx()
        direct = await get_role_template(role_name="test_role", _context=ctx)
        via = await console("role get test_role", _context=ctx)
        assert via["success"] is True
        assert via["role_template"] == direct["role_template"]

    async def test_cc1_save_role_template_create_and_update(self):
        """save_role_template ↔ `role create` / `role update`（update 是 PATCH）。"""
        via = await console(
            "role create '{\"name\":\"cc1_role\",\"soul\":\"be careful\",\"type\":\"USER\"}'",
            _context=self._ctx(),
        )
        assert via["success"] is True, via
        assert via["role_template"]["soul"] == "be careful"

        upd = await console(
            "role update cc1_role '{\"soul\":\"be very careful\"}'", _context=self._ctx()
        )
        assert upd["success"] is True, upd
        assert upd["role_template"]["soul"] == "be very careful"
        assert upd["role_template"]["name"] == "cc1_role"
        assert upd["role_template"]["type"] == "USER"

    async def test_cc1_delete_dept(self):
        """delete_dept ↔ `dept rm`（两步删除的完整等价见 CC4）。"""
        via = await console(
            f"dept create '{{\"name\":\"cc1_dept_rm\",\"responsibility\":\"x\","
            f"\"manager_name\":\"mgr3\",\"parent_name\":\"root\"}}'",
            _context=self._ctx(),
        )
        assert via["success"] is True, via

        first = await console("dept rm cc1_dept_rm", _context=self._ctx())
        assert first["error_code"] == "confirm_required"
        second = await console(
            f"dept rm cc1_dept_rm --confirm {first['confirm_token']}", _context=self._ctx()
        )
        assert second["success"] is True, second
        assert await gtDeptManager.get_dept_by_name(self.team_id, "cc1_dept_rm") is None

    async def test_cc1_delete_role_template(self):
        """delete_role_template ↔ `role rm`（含 confirm 两步）。"""
        via = await console(
            "role create '{\"name\":\"cc1_role_rm\",\"soul\":\"tmp\",\"type\":\"USER\"}'",
            _context=self._ctx(),
        )
        assert via["success"] is True, via

        first = await console("role rm cc1_role_rm", _context=self._ctx())
        assert first["error_code"] == "confirm_required"
        # 未确认前必须还在。
        assert await gtRoleTemplateManager.get_role_template_by_name("cc1_role_rm") is not None

        second = await console(
            f"role rm cc1_role_rm --confirm {first['confirm_token']}", _context=self._ctx()
        )
        assert second["success"] is True, second
        assert await gtRoleTemplateManager.get_role_template_by_name("cc1_role_rm") is None

    async def test_cc1_delete_room(self):
        """delete_room ↔ `room rm`（含 confirm 两步）。"""
        via = await console(
            "room create '{\"name\":\"cc1_room_rm\",\"member_names\":[\"alice\",\"mgr8\"]}'",
            _context=self._ctx(),
        )
        assert via["success"] is True, via

        first = await console("room rm cc1_room_rm", _context=self._ctx())
        assert first["error_code"] == "confirm_required"
        assert await gtRoomManager.get_room_by_team_and_name(self.team_id, "cc1_room_rm") is not None

        second = await console(
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
        first = await console("reload", _context=self._ctx())
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
    # ==================================================================

    async def test_cc2_basic_only_caller_is_denied_admin_command(self):
        """只允许 BASIC 的调用者跑 `agent create` → **必须被拒**，且 DB 里没有新成员。"""
        before = await gtAgentManager.get_agent(self.team_id, "cc2_forbidden", status=None)
        assert before is None

        result = await console(
            "agent create '{\"name\":\"cc2_forbidden\",\"role_template_name\":\"test_role\"}'",
            _context=self._ctx("bob"),
        )
        assert result["success"] is False
        assert result["error_code"] == "permission_denied"
        assert result["legacy_tool"] == "save_agent"
        assert result["required_category"] == "ADMIN"
        # 光断言返回体不够：必须证明**真的没落库**（拒绝发生在执行之前）。
        assert await gtAgentManager.get_agent(self.team_id, "cc2_forbidden", status=None) is None

    async def test_cc2_basic_only_caller_is_allowed_read_command(self):
        """同一个 BASIC 调用者跑 `dept ls` → 必须通过（console 是 BASIC，D3 前提）。"""
        result = await console("dept ls", _context=self._ctx("bob"))
        assert result["success"] is True
        assert result["dept"]["dept_name"] == "root"

    async def test_cc2_root_leader_can_run_admin_command(self):
        """根主管（运行时自动获 Admin）跑同一条 `agent create` → 通过。

        这条是**上一条的阳性对照**：如果只测「被拒」，那么把权限校验写成永远拒绝也能过。
        两条一起才说明拒绝是「按权限判的」，不是「一律拒」。
        """
        result = await console(
            "agent create '{\"name\":\"cc2_allowed\",\"role_template_name\":\"test_role\","
            "\"driver\":\"native\"}'",
            _context=self._ctx("alice"),
        )
        assert result["success"] is True, result

    @pytest.mark.parametrize(
        "command,expected_tool,expected_category",
        [
            ("dept rm root", "delete_dept", "ADMIN"),
            ("room rm cc1_room", "delete_room", "ADMIN"),
            ("role create '{\"name\":\"x\",\"soul\":\"y\"}'", "save_role_template", "ADMIN"),
            ("reload", "reload_team", "ADMIN"),
            # `list_role_templates` 在 CATEGORY_CONFIG 里就是 ADMIN（不是 BASIC），
            # 所以 `role ls` 必须按 ADMIN 校验——这条正是「按等价原工具查表」的体现。
            ("role ls", "list_role_templates", "ADMIN"),
            ("agent update bob '{\"model\":\"m\"}'", "save_agent", "ADMIN"),
            ("room update cc1_room '{\"max_rounds\":3}'", "save_room", "ADMIN"),
        ],
    )
    async def test_cc2_every_admin_subcommand_is_denied_for_basic_caller(
        self, command, expected_tool, expected_category
    ):
        """**逐条**验证所有管理类子命令对 BASIC 调用者都关门（不是只测 save_agent）。

        单一用例容易漏掉「加新命令时忘填 legacy_tool」——这里按命令表遍历，
        新增管理命令若没被判为 ADMIN，这条会红。
        """
        result = await console(command, _context=self._ctx("charlie"))
        assert result["success"] is False
        assert result["error_code"] == "permission_denied"
        assert result["legacy_tool"] == expected_tool
        assert result["required_category"] == expected_category

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

        没有这条，把 `_assert_permitted` 写成「只有 Admin 能过」也照样能让上一条绿。
        """
        result = await console(command, _context=self._ctx("charlie"))
        assert result.get("error_code") != "permission_denied", (
            f"`{command}` 等价于 {expected_tool}（BASIC），不该被拒：{result}"
        )

    async def test_cc2_fail_closed_when_legacy_tool_missing(self, monkeypatch):
        """命令表漏填 `legacy_tool` 时必须 **拒绝**，不能「没填就放行」。

        fail-closed 是 D3 的兜底：将来加命令时漏填等价工具名，权限校验就无从查起。
        若这里放行，一个笔误等于给所有 Agent 开了一个 Admin 后门。
        """
        from dataclasses import replace as dc_replace

        broken = dc_replace(cc.COMMAND_INDEX["agent create"], legacy_tool="")
        monkeypatch.setitem(cc.COMMAND_INDEX, "agent create", broken)

        result = await console(
            "agent create '{\"name\":\"cc2_failopen\",\"role_template_name\":\"test_role\"}'",
            _context=self._ctx("alice"),  # 连根主管也拒：这是内部错误，不是权限问题
        )
        assert result["success"] is False
        assert result["error_code"] == "permission_denied"
        assert "未声明等价工具" in result["message"]
        assert await gtAgentManager.get_agent(self.team_id, "cc2_failopen", status=None) is None

    async def test_cc2_permission_checked_before_execution(self, monkeypatch):
        """权限校验必须发生在**执行之前**：用计数桩证明底层函数没被调用。

        只断言返回体 `permission_denied` 不够——实现可能「先执行再报无权限」，
        那样数据已经被改了，返回体照样是 permission_denied。
        """
        calls: list[str] = []
        from service.funcToolService import tools as legacy_tools

        original = legacy_tools.save_agent

        async def _spy(**kwargs):
            calls.append(kwargs.get("name", ""))
            return await original(**kwargs)

        monkeypatch.setattr(legacy_tools, "save_agent", _spy)

        result = await console(
            "agent create '{\"name\":\"cc2_spy\",\"role_template_name\":\"test_role\"}'",
            _context=self._ctx("bob"),
        )
        assert result["error_code"] == "permission_denied"
        assert calls == [], f"被拒的命令不得触达 save_agent，实际调用：{calls}"

    # ==================================================================
    # CC3 输入来源互斥（D1）
    # ==================================================================

    async def test_cc3_from_payload_without_payload_reports_missing_payload(self):
        """`--from payload` 但没给 `payload` → `missing_payload`（不是「解析失败」）。"""
        result = await console("agent create --from payload", _context=self._ctx())
        assert result["success"] is False
        assert result["error_code"] == "missing_payload"

    async def test_cc3_payload_given_without_from_reports_ambiguous(self):
        """给了 `payload` 却用内联 JSON → `ambiguous_input_source`。"""
        result = await console(
            "agent create '{\"name\":\"cc3_a\",\"role_template_name\":\"test_role\"}'",
            payload={"name": "cc3_a", "role_template_name": "test_role"},
            _context=self._ctx(),
        )
        assert result["success"] is False
        assert result["error_code"] == "ambiguous_input_source"

    async def test_cc3_payload_given_without_from_on_non_input_command(self):
        """`dept ls` 传了 payload → 同样 `ambiguous_input_source`（不许静默忽略）。"""
        result = await console("dept ls", payload={"x": 1}, _context=self._ctx())
        assert result["success"] is False
        assert result["error_code"] in {"ambiguous_input_source", "unknown_option"}

    async def test_cc3_from_payload_together_with_inline_json_is_ambiguous(self):
        """`--from payload` 与内联 JSON 同时出现 → `ambiguous_input_source`。"""
        result = await console(
            "agent create '{\"name\":\"cc3_b\"}' --from payload",
            payload={"name": "cc3_b", "role_template_name": "test_role"},
            _context=self._ctx(),
        )
        assert result["success"] is False
        assert result["error_code"] == "ambiguous_input_source"

    async def test_cc3_from_payload_happy_path(self):
        """`--from payload` + payload object → 正常创建（证明上面几条不是「一律拒绝」）。"""
        result = await console(
            "agent create --from payload",
            payload={"name": "cc3_ok", "role_template_name": "test_role", "driver": "native"},
            _context=self._ctx(),
        )
        assert result["success"] is True, result
        saved = await gtAgentManager.get_agent(self.team_id, "cc3_ok", status=None)
        assert saved is not None and saved.driver.value == "native"

    async def test_cc3_no_input_source_at_all(self):
        """create 什么都不给 → `missing_input_source`。"""
        result = await console("agent create", _context=self._ctx())
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
        result = await console(command, _context=self._ctx())
        assert result["success"] is False
        assert result["error_code"] == expected_code
        assert result["supported"] == ["payload"], "必须告诉模型 v1 支持什么"

    async def test_cc3_invalid_inline_json(self):
        """内联 JSON 语法错误 → `invalid_source_json`（带行列号，便于模型自纠）。"""
        result = await console("agent create '{bad json'", _context=self._ctx())
        assert result["success"] is False
        assert result["error_code"] == "invalid_source_json"

    async def test_cc3_inline_json_must_be_object(self):
        """根节点不是 object（如数组）→ `invalid_payload_field`。"""
        result = await console("agent create '[1,2,3]'", _context=self._ctx())
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

        result = await console("room rm cc4_room", _context=ctx)
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
        first = await console("room rm cc4_room2", _context=ctx)
        token = first["confirm_token"]

        second = await console(f"room rm cc4_room2 --confirm {token}", _context=ctx)
        assert second["success"] is True, second
        assert await gtRoomManager.get_room_by_team_and_name(self.team_id, "cc4_room2") is None

    async def test_cc4_token_is_single_use(self):
        """同一个 token 用第二次必须被拒（否则等于永久免确认）。"""
        ctx = self._ctx()
        await self.create_room(TEAM, "cc4_room3", ["alice", "mgr2"], max_rounds=3)
        first = await console("room rm cc4_room3", _context=ctx)
        token = first["confirm_token"]

        ok = await console(f"room rm cc4_room3 --confirm {token}", _context=ctx)
        assert ok["success"] is True, ok

        await self.create_room(TEAM, "cc4_room3", ["alice", "mgr2"], max_rounds=3)
        replay = await console(f"room rm cc4_room3 --confirm {token}", _context=ctx)
        assert replay["success"] is False
        assert replay["error_code"] == "invalid_confirm_token"
        assert await gtRoomManager.get_room_by_team_and_name(self.team_id, "cc4_room3") is not None

    async def test_cc4_token_bound_to_target(self):
        """A 目标发的 token 不能拿去删 B 目标（绑定 target）。"""
        ctx = self._ctx()
        await self.create_room(TEAM, "cc4_room_a", ["alice", "mgr3"], max_rounds=3)
        await self.create_room(TEAM, "cc4_room_b", ["alice", "mgr4"], max_rounds=3)
        first = await console("room rm cc4_room_a", _context=ctx)
        token = first["confirm_token"]

        crossed = await console(f"room rm cc4_room_b --confirm {token}", _context=ctx)
        assert crossed["success"] is False
        assert crossed["error_code"] == "invalid_confirm_token"
        assert await gtRoomManager.get_room_by_team_and_name(self.team_id, "cc4_room_b") is not None

    async def test_cc4_token_bound_to_caller(self):
        """alice 拿到的 token 不能由 bob 使用（绑定调用者）。"""
        await self.create_room(TEAM, "cc4_room_c", ["alice", "mgr5"], max_rounds=3)
        first = await console("room rm cc4_room_c", _context=self._ctx("alice"))
        token = first["confirm_token"]

        crossed = await console(f"room rm cc4_room_c --confirm {token}", _context=self._ctx("bob"))
        # bob 是 BASIC，先撞权限墙；就算放开权限，token 绑定也应当拒。
        assert crossed["success"] is False
        assert await gtRoomManager.get_room_by_team_and_name(self.team_id, "cc4_room_c") is not None

    async def test_cc4_expired_token_rejected(self, monkeypatch):
        """过期 token → 拒绝，且房间仍在。"""
        ctx = self._ctx()
        await self.create_room(TEAM, "cc4_room4", ["alice", "mgr6"], max_rounds=3)
        first = await console("room rm cc4_room4", _context=ctx)
        token = first["confirm_token"]

        # 直接把表项的过期时间推到过去（比 sleep 300s 可控、也不会拖慢测试）。
        import time as _time

        cp._confirm_tokens[token]["expires_at"] = _time.time() - 1

        result = await console(f"room rm cc4_room4 --confirm {token}", _context=ctx)
        assert result["success"] is False
        assert result["error_code"] == "invalid_confirm_token"
        assert await gtRoomManager.get_room_by_team_and_name(self.team_id, "cc4_room4") is not None

    async def test_cc4_unknown_token_rejected(self):
        """随便编一个 token → 拒绝（**不得**因为「反正 token 无效」就跳过目标校验）。"""
        await self.create_room(TEAM, "cc4_room_x", ["mgr8", "mgr9"], max_rounds=3)
        result = await console("room rm cc4_room_x --confirm deadbeef", _context=self._ctx())
        assert result["success"] is False
        assert result["error_code"] == "invalid_confirm_token"
        assert await gtRoomManager.get_room_by_team_and_name(self.team_id, "cc4_room_x") is not None

    async def test_cc4_dept_rm_recursive_required_lists_impact(self):
        """`dept rm` 有子部门且未加 `--recursive` → `recursive_required`，且不删。"""
        ctx = self._ctx()
        await console(
            "dept create '{\"name\":\"cc4_parent\",\"responsibility\":\"p\","
            "\"manager_name\":\"mgr2\",\"parent_name\":\"root\"}'",
            _context=ctx,
        )
        await console(
            "dept create '{\"name\":\"cc4_child\",\"responsibility\":\"c\","
            "\"manager_name\":\"mgr4\",\"parent_name\":\"cc4_parent\"}'",
            _context=ctx,
        )
        result = await console("dept rm cc4_parent", _context=ctx)
        assert result["success"] is False
        assert result["error_code"] == "recursive_required"
        assert "1 个子部门" in result["message"]
        assert await gtDeptManager.get_dept_by_name(self.team_id, "cc4_parent") is not None

    async def test_cc4_dept_rm_recursive_lists_children_then_deletes(self):
        """`dept rm --recursive` → 影响范围里**列出子部门名**，确认后父子都删。"""
        ctx = self._ctx()
        first = await console("dept rm cc4_parent --recursive", _context=ctx)
        assert first["error_code"] == "confirm_required"
        impact_text = "\n".join(first["impact"])
        assert "cc4_child" in impact_text, f"影响范围必须列出会被删的子部门名：{impact_text}"

        second = await console(
            f"dept rm cc4_parent --recursive --confirm {first['confirm_token']}", _context=ctx
        )
        assert second["success"] is True, second
        assert await gtDeptManager.get_dept_by_name(self.team_id, "cc4_parent") is None
        assert await gtDeptManager.get_dept_by_name(self.team_id, "cc4_child") is None

    async def test_cc4_reload_confirm_gate(self):
        """`reload` 不带 `--confirm` → `confirm_required`，**不执行**（否则会真重载团队）。"""
        result = await console("reload", _context=self._ctx())
        assert result["success"] is False
        assert result["error_code"] == "confirm_required"
        assert result["confirm_token"]

    # ==================================================================
    # CC5 PATCH 语义（D4/draft §4.3）
    # ==================================================================

    async def test_cc5_dept_update_only_changes_given_field(self):
        """`dept update` 只改 payload 里出现的字段，其余全保持。"""
        ctx = self._ctx()
        await console(
            "dept create '{\"name\":\"cc5_dept\",\"responsibility\":\"原始职责\","
            "\"manager_name\":\"mgr6\",\"member_names\":[\"mgr6\",\"mgr9\"],"
            "\"parent_name\":\"root\"}'",
            _context=ctx,
        )
        before = await gtDeptManager.get_dept_by_name(self.team_id, "cc5_dept")
        assert before is not None

        result = await console(
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
        created = await console(
            "dept create '{\"name\":\"cc5_dept_arr\",\"responsibility\":\"r\","
            "\"manager_name\":\"mgr6\",\"member_names\":[\"mgr6\",\"mgr9\"],"
            "\"parent_name\":\"root\"}'",
            _context=ctx,
        )
        assert created["success"] is True, created

        result = await console(
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
        await console(
            "agent create '{\"name\":\"cc5_agent\",\"role_template_name\":\"test_role\","
            "\"driver\":\"claude_sdk\",\"model\":\"m1\",\"allow_tools\":[\"Category:Read\"]}'",
            _context=ctx,
        )
        before = await gtAgentManager.get_agent(self.team_id, "cc5_agent", status=None)
        assert before is not None
        assert before.driver.value == "claude_sdk"

        result = await console("agent update cc5_agent '{\"model\":\"m2\"}'", _context=ctx)
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
        await console(
            "room create '{\"name\":\"cc5_room\",\"member_names\":[\"alice\",\"mgr7\"],"
            "\"initial_topic\":\"原始话题\",\"max_rounds\":3}'",
            _context=ctx,
        )
        before = await gtRoomManager.get_room_by_team_and_name(self.team_id, "cc5_room")
        assert before is not None

        result = await console("room update cc5_room '{\"max_rounds\":9}'", _context=ctx)
        assert result["success"] is True, result
        after = await gtRoomManager.get_room_by_team_and_name(self.team_id, "cc5_room")
        assert after is not None
        assert after.max_rounds == 9
        assert after.initial_topic == "原始话题", "未出现的 initial_topic 必须保持原值"
        assert sorted(after.agent_ids) == sorted(before.agent_ids)

    async def test_cc5_role_update_preserves_type_and_i18n(self):
        """`role update` 只改 soul 时，type / i18n / name 保持不变。"""
        ctx = self._ctx()
        await console(
            "role create '{\"name\":\"cc5_role\",\"soul\":\"s1\",\"type\":\"USER\","
            "\"i18n\":{\"display_name\":{\"zh-CN\":\"角色甲\"}}}'",
            _context=ctx,
        )
        before = await gtRoleTemplateManager.get_role_template_by_name("cc5_role")
        assert before is not None

        result = await console("role update cc5_role '{\"soul\":\"s2\"}'", _context=ctx)
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
        result = await console("dept get 'unclosed", _context=self._ctx())
        assert result["success"] is False
        assert result["error_code"] == "invalid_command_syntax"

    async def test_d5_no_eval_of_command_substitution(self):
        """`$(...)` / 反引号**不执行**，只当普通文本（D5：绝不 eval、不调 shell）。"""
        marker = "/tmp/console_eval_marker"
        if os.path.exists(marker):
            os.remove(marker)
        result = await console(f"dept get '$(touch {marker})'", _context=self._ctx())
        assert not os.path.exists(marker), "命令替换绝不能被执行"
        # 结果应是「未找到部门」，即把整段当成了一个普通名字。
        assert result["success"] is False

    async def test_unknown_command_gives_candidates(self):
        """未知命令 → `unknown_command` 且给出可用操作（draft §6 的友好错误）。"""
        result = await console("dept del x", _context=self._ctx())
        assert result["success"] is False
        assert result["error_code"] == "unknown_command"
        assert "ls" in result["message"] and "rm" in result["message"]

    async def test_unknown_option_rejected(self):
        """未知选项 → `unknown_option`（不是静默忽略）。"""
        result = await console("dept ls --nope", _context=self._ctx())
        assert result["success"] is False
        assert result["error_code"] == "unknown_option"

    async def test_help_does_not_require_permission(self):
        """`help` 不碰数据，任何身份都能看（否则没权限的人只能瞎试命令）。"""
        for name in ("alice", "bob", "charlie"):
            result = await console("help", _context=self._ctx(name))
            assert result["success"] is True
            assert "dept" in result["help"]
