"""`console` 的 `task` 资源命令（#187）：从**真 console 入口**走到底。

判据出处：任务单 #187 §3 与 #0 裁定记录（事件 978）。

为什么要单开一个文件、而且必须是**集成**（真库）级别：
本单最容易漏的一条是「入口可达性」——命令写了、路由写了，但没注册 / 没接进
`COMMAND_SPECS` / 第 2 跳查不到目标，一切都静默地不生效。console 工具历史上出过
「只测内部层，注册漏了也没报错」的事故，所以这里的每一条用例都走
`AgentToolRegistry.execute_tool_call`（= 生产上唯一执行入口），
路径完整为：console 入口 → 解析 → `_route` → `_forward` → 第 2 跳权限 → 原工具 → taskService → DB。

**不测** `_route` 单独调用，也不测 `taskService` 单独调用：那两条都在本单的改动之外，
且它们绿了也不能证明「从 console 敲 `task update` 真的能改库」。

一条口径说明（为什么断言要打到 DB 与错误码，而不是只看 `success`）：
`console` 的价值是**等价**。若它把 `--remove-field reviewer_id` 吞掉却仍返回
`success: True`，只看 success 完全发现不了——真正的判据必须是「库里的 reviewer_id
真的被置空了」和「错误码真的是那一个」。
"""

from __future__ import annotations

import os
import sys

import pytest

import service.ormService as ormService
from constants import ALLOWED_CLEAR_FIELDS, TaskStatus
from dal.db import gtAgentManager, gtAgentTaskManager, gtRoleTemplateManager, gtTeamManager
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtAgentTask import GtAgentTask
from model.dbModel.gtRoleTemplate import GtRoleTemplate
from model.dbModel.gtTeam import GtTeam
from service import funcToolService
from service.agentService.toolRegistry import AgentToolRegistry, build_runtime_allow_specs
from service.funcToolService import consoleRegistry
from service.funcToolService import core as func_tool_core
from service.roomService import ToolCallContext
from util.llmApiUtil import OpenAIToolCall

from ...base import ServiceTestCase
from ...integration.task_helpers import create_task_with_event

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

TEAM = "console_task_team"


@pytest.fixture(autouse=True)
def _restore_clear_field_enum():
    """白名单扩展类用例会 monkeypatch `tools.ClearField`，必须让 schema 缓存不跨用例残留。

    schema 缓存按可调用对象身份 + 注解指纹存储；`monkeypatch` 恢复注解后指纹回到原值，
    键仍是原来那个 ⇒ 命中「扩展版 enum」的旧缓存就会让后面的用例假绿/假红。
    """

    from service.funcToolService import toolArgsValidation

    toolArgsValidation.clear_schema_cache()
    yield
    toolArgsValidation.clear_schema_cache()


def _tool_call(command: str, payload: dict | None = None) -> OpenAIToolCall:
    args: dict = {"command": command}
    if payload is not None:
        args["payload"] = payload
    return OpenAIToolCall(
        id="call_1",
        function={"name": "console", "arguments": __import__("json").dumps(args, ensure_ascii=False)},
    )


def _build_registry() -> AgentToolRegistry:
    """构造与生产 driver 同形的注册表（照抄 `test_console_tool._build_registry`）。

    用 `load_func_tools()` 而非 `get_tools()`：后者受 D2 开关影响会隐藏原工具，
    而**执行侧必须始终可用**（console 第 2 跳要查得到），这是 D2 的约定。
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
    registry.apply_tool_allow_specs(build_runtime_allow_specs(None, is_root_leader=True))
    return registry


class _ConsoleTaskEntryBase(ServiceTestCase):
    """共享基座：真 console 入口 → 解析 → 路由 → 第 2 跳 → DB 的 setup 与辅助方法。

    类名不以 `Test` 开头 ⇒ 不被收集。拆出基座是为了让 need_fix_4 的
    `TestConsoleArrayCliNeedFix4` 不必继承 `TestConsoleTaskCommand`：否则父类的
    35 条用例会被子类重复收集、整套重跑一遍（判据一样，纯属浪费）。
    """

    @classmethod
    async def async_setup_class(cls):
        await ormService.startup(cls._get_test_db_path())

        team = await gtTeamManager.save_team(GtTeam(name=TEAM))
        cls.team_id = team.id
        template = await gtRoleTemplateManager.save_role_template(
            GtRoleTemplate(name="console_task_role", soul="soul")
        )
        await gtAgentManager.batch_save_agents(
            team.id,
            [
                GtAgent(team_id=team.id, name="owner", role_template_id=template.id),
                GtAgent(team_id=team.id, name="worker", role_template_id=template.id),
                GtAgent(team_id=team.id, name="rev_a", role_template_id=template.id),
                GtAgent(team_id=team.id, name="rev_b", role_template_id=template.id),
                GtAgent(team_id=team.id, name="outsider", role_template_id=template.id),
            ],
        )
        agents = await gtAgentManager.get_team_all_agents(team.id)
        cls.ids = {a.name: a.id for a in agents}
        cls.registry = _build_registry()

    @classmethod
    async def async_teardown_class(cls):
        await ormService.shutdown()

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    def _ctx(self, name: str = "owner") -> ToolCallContext:
        return ToolCallContext(
            agent_id=self.ids[name], team_id=self.team_id, chat_room=None
        )

    async def _call(self, command: str, payload: dict | None = None, *, who: str = "owner") -> dict:
        """经**唯一执行入口**发一次 console 调用（这就是判据 1 要求的那条路径）。"""
        result = await self.registry.execute_tool_call(
            _tool_call(command, payload), self._ctx(who)
        )
        return result.result

    async def _make_task(
        self,
        *,
        status: TaskStatus = TaskStatus.TODO,
        reviewer_id: int | None = None,
        title: str = "console task",
    ) -> GtAgentTask:
        task = GtAgentTask(
            team_id=self.team_id,
            title=title,
            assignee_id=self.ids["worker"],
            creator_id=self.ids["owner"],
            reviewer_id=reviewer_id,
            status=status,
        )
        return await create_task_with_event(task)

    async def _reload(self, task_id: int) -> GtAgentTask:
        """从库里重读任务。**断言必须打在库上**，不能信返回体。"""
        row = await gtAgentTaskManager.get_task(task_id)
        assert row is not None
        return row

class TestConsoleTaskCommand(_ConsoleTaskEntryBase):
    """`task ls|get|update` 从真 console 入口到 DB 的端到端行为。"""

    # ==================================================================
    # 判据 1：入口可达性 —— 真 console 入口 → 解析 → 路由 → 第 2 跳 → DB
    # ==================================================================

    async def test_entry_reachable_task_update_remove_field_writes_db(self):
        """`task update <id> --remove-field reviewer_id` **真的把库里的 reviewer_id 置空**。

        这是本单最核心的一条：它同时覆盖
        - 命令注册（工具声明派生出的命令表里有 task update，见下面 `test_task_commands_*`）、
        - 配置输入可选（没有配置输入也不会报 `missing_input_source`）、
        - `--remove-field` → `clear_fields` 的映射、
        - 第 2 跳权限与 `taskService` 的撤销分支、
        - 落库。
        任何一环断掉，`reviewer_id` 都不会变成 None（或者干脆报错），断言即红。
        """
        task = await self._make_task(reviewer_id=self.ids["rev_a"])
        assert task.reviewer_id == self.ids["rev_a"], "前置：库里应记着验收人"

        result = await self._call(f"task update {task.id} --remove-field reviewer_id")

        assert result["success"] is True, f"console 入口失败：{result}"
        assert result["action"] == "reviewer_cleared" or result.get("new_reviewer_id") is None, (
            f"返回体不像撤销结果：{result}"
        )
        row = await self._reload(task.id)
        assert row.reviewer_id is None, f"库里 reviewer_id 仍为 {row.reviewer_id}，--remove-field 未生效"

    async def test_entry_reachable_task_get_reads_db(self):
        """`task get <id>` 从真入口拿到的是**这个**任务的详情（不是什么替代返回值）。"""
        task = await self._make_task(title="独一无二的标题-abc123")
        result = await self._call(f"task get {task.id}")
        assert result["success"] is True, f"console 入口失败：{result}"
        # 不打在「有 task 字段」这种弱断言上：必须是这个 id 与这个标题。
        assert result["task"]["id"] == task.id
        assert result["task"]["title"] == "独一无二的标题-abc123"

    async def test_entry_reachable_task_ls_filters(self):
        """`task ls --assignee <id>` 从真入口返回该执行人的任务，且不含别人的。"""
        mine = await self._make_task(title="我的任务-mine")
        other = GtAgentTask(
            team_id=self.team_id,
            title="别人的任务-other",
            assignee_id=self.ids["outsider"],
            creator_id=self.ids["owner"],
            status=TaskStatus.TODO,
        )
        await create_task_with_event(other)

        result = await self._call(f"task ls --assignee {self.ids['worker']}")
        assert result["success"] is True, f"console 入口失败：{result}"
        titles = [t["title"] for t in result["tasks"]]
        assert "我的任务-mine" in titles
        assert "别人的任务-other" not in titles, f"--assignee 未生效，收到 {titles}"
        assert result["tasks"][0]["id"] == mine.id

    async def test_task_ls_status_csv_is_passed_through(self):
        """`--status TODO,DONE` 逗号分隔 → 服务层按**多个**状态过滤（不是只认第一个）。"""
        done = await self._make_task(status=TaskStatus.DONE, title="已完成-done")
        todo = await self._make_task(status=TaskStatus.TODO, title="待办-todo")
        held = await self._make_task(status=TaskStatus.ON_HOLD, title="搁置-hold")

        result = await self._call(f"task ls --status TODO,DONE --assignee {self.ids['worker']}")
        assert result["success"] is True, f"console 入口失败：{result}"
        ids = {t["id"] for t in result["tasks"]}
        assert done.id in ids and todo.id in ids, f"TODO/DONE 应都命中，实收 {sorted(ids)}"
        assert held.id not in ids, "ON_HOLD 不该出现在 --status TODO,DONE 的结果里"

    async def test_task_ls_kw_maps_to_title_keyword(self):
        """`--kw` 必须映射到 `title_keyword`（选项名与工具参数名不同，靠显式映射）。"""
        hit = await self._make_task(title="登录失败排查-hitkw")
        await self._make_task(title="完全无关的标题")

        result = await self._call(f"task ls --kw 登录失败排查 --assignee {self.ids['worker']}")
        assert result["success"] is True, f"console 入口失败：{result}"
        ids = {t["id"] for t in result["tasks"]}
        assert ids == {hit.id}, f"--kw 未映射到 title_keyword，命中 {sorted(ids)}"

    # ==================================================================
    # 判据 2：权限第 2 跳 —— console 路径**没有绕过**服务层权限
    # ==================================================================

    async def test_permission_hop_not_bypassed_non_creator_cannot_clear(self):
        """非创建人撤销验收人 ⇒ 必须被服务层拒绝（`permission_denied`），库不变。

        撤销验收人限创建人（`taskService._handle_reviewer_change` 的 D2 分支）。
        这里从 console 入口以「非创建人、非执行人」的 outsider 身份调用：
        若 console 自己算了一遍权限并放行，本用例就抓不到——所以必须断言
        **错误码**与**库未变**两件事，而不是只看 success=False。
        """
        task = await self._make_task(reviewer_id=self.ids["rev_a"])

        result = await self._call(
            f"task update {task.id} --remove-field reviewer_id", who="outsider"
        )

        assert result["success"] is False, f"非创建人竟撤销成功：{result}"
        assert result["error_code"] == "permission_denied", (
            f"期望服务层的 permission_denied，收到 {result.get('error_code')!r}（{result.get('message')!r}）"
            "——若错误码变了，可能是 console 侧另写了一份权限判定，必须复核"
        )
        row = await self._reload(task.id)
        assert row.reviewer_id == self.ids["rev_a"], "被拒绝的调用仍改了库"

    async def test_permission_hop_not_bypassed_reviewing_cannot_clear(self):
        """REVIEWING 状态下即使是创建人也不能撤销（否则无人能通过验收）。"""
        task = await self._make_task(status=TaskStatus.REVIEWING, reviewer_id=self.ids["rev_a"])
        result = await self._call(f"task update {task.id} --remove-field reviewer_id")
        assert result["success"] is False, f"REVIEWING 竟撤销成功：{result}"
        assert result["error_code"] == "reviewer_required_in_reviewing", (
            f"期望 reviewer_required_in_reviewing，收到 {result.get('error_code')!r}"
        )
        row = await self._reload(task.id)
        assert row.reviewer_id == self.ids["rev_a"]

    # ==================================================================
    # 判据 3a：白名单**单一事实源**（静态哨兵 + 行为判据）
    # ==================================================================

    def test_console_files_have_no_hardcoded_clear_field_whitelist(self):
        """console 三文件里**不得**出现白名单字面量集合（静态哨兵）。

        为什么要有这条：`--remove-field` 的白名单由服务层校验，console 只做映射。
        若有人图省事在 console 侧写一份 `{"reviewer_id"}` 并先用它校验，
        扩 `ClearField` 时两处就会漂移——#181 现场正是这样翻的车（`taskService`
        里残留一份手写副本遮蔽了派生常量，扩 enum 对运行时毫无影响，
        而变异自证照样「红」，红错了理由）。

        判据形式：搜「成对出现的白名单字面量集合语法」，即同时含
        `reviewer_id` 的花括号/方括号集合字面量。**故意不用「文件里出现
        reviewer_id 就红」**——`reviewer_id` 作为转发参数名必须出现（它就是
        `update_task` 的关键字参数），那种写法会把正确代码也判红。
        """
        import re
        from pathlib import Path

        base = Path(__file__).resolve().parents[3] / "src" / "service" / "funcToolService"
        files = ["consoleTool.py", "consoleCommands.py", "consoleParsing.py"]

        # 匹配 `{"reviewer_id"}` / `{'reviewer_id'}` / `["reviewer_id"]` 这类集合字面量。
        pattern = re.compile(r"""\{\s*['"]reviewer_id['"]\s*\}""")
        offenders = []
        for name in files:
            text = (base / name).read_text(encoding="utf-8")
            for match in pattern.finditer(text):
                line_no = text[: match.start()].count("\n") + 1
                offenders.append(f"{name}:{line_no}  {match.group(0)}")

        assert not offenders, (
            "console 侧出现了硬编码的白名单集合字面量，白名单必须只由 "
            "`constants.ALLOWED_CLEAR_FIELDS` 一处派生：\n  " + "\n  ".join(offenders)
        )

    def test_remove_field_help_candidates_derive_from_constants(self, monkeypatch):
        """help 里 `--remove-field` 的候选字段**随 `ALLOWED_CLEAR_FIELDS` 变化**（行为判据）。

        静态哨兵只能证明「没抄一份字面量」，证明不了「真的用了那个常量」——
        有人可以写成 `" / ".join(["reviewer_id"])` 绕过正则。所以这里扩
        `ALLOWED_CLEAR_FIELDS` 后，help 文案必须跟着出现新字段名；不跟着变就说
        明候选集合另有来源（又抄了一份）。
        """
        from service.funcToolService import consoleTool as ct

        baseline = ct._help_text(["help", "task", "update"])["text"]
        assert "reviewer_id" in baseline

        extended = frozenset(set(ALLOWED_CLEAR_FIELDS) | {"zz_fake_field"})
        monkeypatch.setattr(ct, "ALLOWED_CLEAR_FIELDS", extended, raising=True)

        after = ct._help_text(["help", "task", "update"])["text"]
        assert "zz_fake_field" in after, (
            "扩了 ALLOWED_CLEAR_FIELDS 但 help 候选没跟着变 ⇒ 候选集合不是从该常量派生的。\n"
            f"扩前：{baseline!r}\n扩后：{after!r}"
        )

    # ==================================================================
    # 判据 3b：野值拒绝走**运行时白名单**（console 不校验、只透传）
    # ==================================================================

    async def test_remove_field_invalid_value_rejected_by_service(self):
        """`--remove-field title` ⇒ 被拒，且候选里带真白名单。

        ⚠️ #189 之后错误码是 `invalid_tool_args`，不再是 `invalid_clear_field`：
        `update_task(clear_fields: Optional[list[ClearField]])` 的注解自动生成了
        `enum: ["reviewer_id"]` 并下发给模型，所以野值在 `run_tool_call` 的 schema 校验
        处就被拦下（第 2 跳），根本到不了 `taskService` 的白名单判定。这是 Operator
        02:24「console 只查自己的 schema，剩下的转发之后检查」的直接后果，不是回归。

        三条实质断言照旧（拒绝 / 消息里能看到合法候选 / 库未变），只把错误码换成
        上游那一层的码——判据强度不变。
        """
        task = await self._make_task(reviewer_id=self.ids["rev_a"])
        result = await self._call(f"task update {task.id} --remove-field title")

        assert result["success"] is False, f"野值竟被接受：{result}"
        assert result["error_code"] == "invalid_tool_args", (
            f"期望 schema 层 invalid_tool_args，收到 {result.get('error_code')!r}"
        )
        for field in ALLOWED_CLEAR_FIELDS:
            assert field in result["message"], (
                f"错误消息未给出候选字段 {field}：{result['message']!r}"
            )
        row = await self._reload(task.id)
        assert row.reviewer_id == self.ids["rev_a"], "被拒绝的调用改了库"

    async def test_remove_field_whitelist_extension_changes_service_behaviour(self, monkeypatch):
        """扩白名单 ⇒ 该字段不再被判野值，且 console 侧零改动跟着生效。

        这条是「白名单单一事实源」的行为侧证明，方向与上一条相反：
        上一条证明野值被拒，这条证明**白名单本身可扩**、且扩了之后 console 侧
        不需要任何改动就跟着生效（因为 console 没有第二份白名单）。

        ⚠️ #189 起白名单有**两层**，扩一层不够（原实现只扩 `taskService` 侧，
        于是被 schema 的 `enum` 先拦下，断言 `!= invalid_clear_field` 恒成立 ⇒ 假绿）：

        1. schema 层：`update_task` 的 `clear_fields` 注解是 `list[ClearField]`，
           `ClearField = Literal["reviewer_id"]` 由 `constants` 派生 ⇒ 下发给模型的
           `enum` 就是它。要让它放行 `zz_fake_field`，必须扩 `constants.ClearField`
           **并清 schema 缓存**（缓存键含注解指纹，`monkeypatch` 换常量对象不会改指纹，
           不清缓存会继续用旧 enum）。
        2. 运行时层：`taskService.ALLOWED_CLEAR_FIELDS`，同样是 constants 派生。

        两层都扩 ⇒ 调用应真正走下去（不再是任何一层报的野值错误）。这条用例因此变成了
        「两层同源、扩一处常量两层同时放行」的证明——比原来只盯运行时层更强。
        """
        import typing

        import constants
        import service.taskService as taskService
        from service.funcToolService import toolArgsValidation
        from service.funcToolService import tools as func_tools

        extended = frozenset(set(ALLOWED_CLEAR_FIELDS) | {"zz_fake_field"})
        # schema 层的源头是 `update_task` 注解 `list[ClearField]`；`tools.py` 用了
        # `from __future__ import annotations`，注解在 `get_type_hints` 时按**该模块的
        # globals** 解析，所以必须 patch `tools.ClearField`（patch `constants` 不生效）。
        monkeypatch.setattr(
            func_tools, "ClearField", typing.Literal["reviewer_id", "zz_fake_field"]
        )
        monkeypatch.setattr(constants, "ALLOWED_CLEAR_FIELDS", extended)
        monkeypatch.setattr(
            taskService, "ALLOWED_CLEAR_FIELDS", extended, raising=True,
        )
        # schema 是从注解**当刻**算出来的，但缓存已热时不会重算；这里显式清缓存，
        # 让判据打在「新注解」上（缓存键含注解指纹，见 toolArgsValidation 注释）。
        toolArgsValidation.clear_schema_cache()

        task = await self._make_task(reviewer_id=self.ids["rev_a"])
        result = await self._call(f"task update {task.id} --remove-field zz_fake_field")

        # 关键：不再报野值错误。它可能因为「假字段清除后什么都没变」而走到别的分支
        # （例如 reviewer_unchanged / 无操作），但**绝不能**是「野值」类错误。
        assert result.get("error_code") not in ("invalid_clear_field", "invalid_tool_args"), (
            f"扩了白名单后仍被判野值 ⇒ console / schema / 服务层用的是另一份固定清单：{result}"
        )

    # ==================================================================
    # 判据 4（配合 M1）：`--remove-field` 必须真的映射到 clear_fields
    # ==================================================================

    async def test_remove_field_does_not_become_reviewer_change(self):
        """`--remove-field reviewer_id` 走的是**撤销**（置空），不是 `reviewer_id=` 改人。

        若实现把它错映射成 `reviewer_id=<某值>`，那就成了「改人」——语义完全相反。
        判据：库里 reviewer_id 变 None，且时间线事件文案以「验收人撤销」开头。
        """
        from dal.db import gtAgentTaskEventManager

        task = await self._make_task(reviewer_id=self.ids["rev_a"])
        result = await self._call(f"task update {task.id} --remove-field reviewer_id")
        assert result["success"] is True, f"console 入口失败：{result}"

        events = await gtAgentTaskEventManager.list_events(task.id, limit=10)
        contents = [e.content for e in events]
        assert any(c.startswith("验收人撤销") for c in contents), (
            f"时间线里没有「验收人撤销」事件，实收：{contents}"
        )

    # ==================================================================
    # 判据（裁定①附加）：空调用必须显式报 `no_update_action`
    # ==================================================================

    async def test_empty_update_is_explicitly_rejected(self):
        """`task update <id>` 既无输入也无 `--remove-field` ⇒ `no_update_action`。

        **不许静默成功**：一个什么都没做的 update 若返回 success，模型会当成
        「已改好」继续往下走，比直接报错贵得多。
        """
        task = await self._make_task(reviewer_id=self.ids["rev_a"])
        result = await self._call(f"task update {task.id}")

        assert result["success"] is False, f"空调用竟成功：{result}"
        assert result["error_code"] == "no_update_action", (
            f"期望 no_update_action，收到 {result.get('error_code')!r}（{result.get('message')!r}）"
        )
        row = await self._reload(task.id)
        assert row.reviewer_id == self.ids["rev_a"], "空调用改了库"

    async def test_empty_note_only_is_rejected(self):
        """只给空 note（`{"note": ""}`）仍算「没有动作」⇒ `no_update_action`。

        不能让「payload 里有个 note 键」就当成有动作：服务层对空 note 的评论
        请求也是拒绝的（`empty_note`），但在 console 侧它更准确的归类是
        「这次调用没有可执行动作」，而且**不该走到服务层去改任何东西**。
        """
        task = await self._make_task()
        result = await self._call(f"task update {task.id} --from payload", payload={"note": "   "})
        assert result["success"] is False, f"空 note 竟成功：{result}"
        assert result["error_code"] == "no_update_action", (
            f"期望 no_update_action，收到 {result.get('error_code')!r}"
        )

    # ==================================================================
    # 裁定⑧：不传 note 不会清空已有 note
    # ==================================================================

    async def test_absent_note_does_not_clear_existing_note(self):
        """`task update <id> '{"new_status": "IN_PROGRESS"}'` 不改动已有事件内容。

        为什么这需要用例：`update_task` 的 `note` 默认是 `''`，若 console 把
        `''` 显式传下去，语义上是「本次没写评论」而不是「清空历史评论」——
        但实现里很容易顺手写成「payload 没 note 就传 None/空串并覆盖」。
        判据：流转后，之前那条评论事件的 content 一字不变。
        """
        from dal.db import gtAgentTaskEventManager

        task = await self._make_task()
        first = await self._call(
            f"task update {task.id} --from payload", payload={"note": "第一版说明-keepme"}
        )
        assert first["success"] is True, f"首次评论失败：{first}"

        # 流转状态只能由执行人发起（服务层口径），这里换成 worker 身份。
        second = await self._call(
            f"task update {task.id} --from payload",
            payload={"new_status": "IN_PROGRESS"},
            who="worker",
        )
        assert second["success"] is True, f"流转失败：{second}"

        events = await gtAgentTaskEventManager.list_events(task.id, limit=20)
        contents = [e.content for e in events]
        assert "第一版说明-keepme" in contents, (
            f"不传 note 的调用清空/改写了已有评论，现有事件内容：{contents}"
        )
        row = await self._reload(task.id)
        assert row.status == TaskStatus.IN_PROGRESS

    # ==================================================================
    # 裁定④：逗号分隔 + 去重保序
    # ==================================================================

    async def test_remove_field_csv_dedup_keeps_order(self):
        """`--remove-field reviewer_id,reviewer_id` ⇒ 去重后仍是 1 个，行为等同单个。

        重复项对「清除字段」没有额外语义，却会让下游「非法字段」报错把同一个
        名字列两遍。这里钉住去重是**保序**的（用两个元素验证，即便当前白名单
        只有 1 个成员，规则也要能扩）。
        """
        from service.funcToolService import consoleTool as ct

        assert ct._split_csv("reviewer_id,reviewer_id") == ["reviewer_id"]
        assert ct._split_csv("b, a ,b,,") == ["b", "a"], "去重必须保序，空段必须丢弃"
        assert ct._split_csv(None) == []
        assert ct._split_csv("  ") == []

        task = await self._make_task(reviewer_id=self.ids["rev_a"])
        result = await self._call(f"task update {task.id} --remove-field reviewer_id,reviewer_id")
        assert result["success"] is True, f"重复值应被去重后正常执行：{result}"
        row = await self._reload(task.id)
        assert row.reviewer_id is None

    async def test_remove_field_csv_with_invalid_value_lists_only_invalid(self):
        """`--remove-field reviewer_id,title` ⇒ 只把 `title` 报为非法（合法项不混进报错）。

        #189 起拒绝发生在 schema 的 `enum` 校验（见上一条说明），错误码为
        `invalid_tool_args`；`title` 是**唯一**被举报的字段值——`reviewer_id` 不在消息里，
        这条「合法项不混进报错」的判据反而更强（enum 校验只报违规元素）。
        """
        task = await self._make_task(reviewer_id=self.ids["rev_a"])
        result = await self._call(f"task update {task.id} --remove-field reviewer_id,title")

        assert result["success"] is False
        assert result["error_code"] == "invalid_tool_args"
        assert "title" in result["message"]
        # 「合法项不混进报错」的判据：只举报**一个**违规元素（枚举合法值列表里当然会
        # 出现 `reviewer_id`，那是候选不是报错项）。
        assert result["message"].count("实际收到") == 1, (
            f"合法项被当成违规项报出：{result['message']!r}"
        )
        assert "clear_fields.1" in result["message"], (
            f"没定位到违规的那一个下标：{result['message']!r}"
        )
        row = await self._reload(task.id)
        assert row.reviewer_id == self.ids["rev_a"], (
            "多值里既有合法又有非法时应整单拒绝、不改库（不能「能清的先清了」）"
        )

    async def test_repeated_option_by_kind_aggregates_errors_and_is_idempotent(self):
        """重复选项的三向语义：数组聚合 / 标量报错 / 布尔幂等（#218 翻 #187 裁定④）。

        **用例沿革**：本条原名 `test_repeated_same_option_keeps_only_the_last`，是
        #187-④ 钉住「同名 option 只认最后一个」的登记用例。Operator 2026-11-14 12:18
        拍板翻案：数组型改为按序聚合，标量型改为**显式报错**（静默丢数据正是被翻掉的
        行为）。用例**未删除**、断言**更强**——从「只验一条覆盖语义」变成三向：

        1. 数组型（`--remove-field` / `--status`）⇒ 按出现顺序聚合、去重保序，不再丢值；
        2. 标量型（`--note`）⇒ `duplicate_option`，库不变（不许静默写后者）；
        3. 布尔开关（`--open-only`）⇒ 幂等，重复不报错。

        旧语义若残留（M1/M2 变异），第 1 或第 2 项必红。
        """
        from service.funcToolService import consoleTool as ct

        # ---- ① 数组型：聚合 + 去重保序（解析层先钉住形状）----
        update_spec = consoleRegistry.get_table().by_path["task update"]
        _pos, options, _src = ct.cp._parse_options(
            update_spec, ["187", "--remove-field", "reviewer_id", "--remove-field", "title"]
        )
        assert options["remove-field"] == ["reviewer_id", "title"], (
            f"数组型重复应聚合为两次出现的有序列表，实际 {options['remove-field']!r}"
            "（若为 'title'，是 #187-④ 的旧覆盖语义回来了）"
        )
        ls_spec = consoleRegistry.get_table().by_path["task ls"]
        _pos, ls_options, _src = ct.cp._parse_options(
            ls_spec, ["--status", "DONE", "--status", "TODO", "--status", "DONE"]
        )
        assert ls_options["status"] == ["DONE", "TODO", "DONE"], (
            f"聚合必须保序且不提前去重，实际 {ls_options['status']!r}"
        )
        # 去重保序由 `_split_csv` 一处负责；聚合出来的**每个元素**仍按 CSV 规则拆逗号，
        # 否则 `--member a,b --member c` 里的 `a,b` 会被当成一个成员名（两个实现分叉）。
        assert ct._split_csv(ls_options["status"]) == ["DONE", "TODO"]
        assert ct._split_csv(["a,b", "c"]) == ["a", "b", "c"], (
            "聚合后的元素没按 CSV 规则拆逗号"
        )

        # 行为层：两次 `--remove-field reviewer_id` 聚合去重后仍是「撤销验收人」，不是丢弃。
        task = await self._make_task(reviewer_id=self.ids["rev_a"])
        result = await self._call(
            f"task update {task.id} --remove-field reviewer_id --remove-field reviewer_id"
        )
        assert result["success"] is True, f"数组型重复应聚合后正常执行：{result}"
        row = await self._reload(task.id)
        assert row.reviewer_id is None, "聚合后 reviewer_id 未被清空"

        # ---- ② 标量型：显式报错，不许静默覆盖 ----
        scalar_task = await self._make_task(title="标量重复-不应被改")
        scalar = await self._call(
            f"task update {scalar_task.id} --note 第一次 --note 第二次"
        )
        assert scalar["success"] is False, f"标量型重复竟静默成功（旧覆盖语义）：{scalar}"
        assert scalar["error_code"] == "duplicate_option", (
            f"期望 duplicate_option，收到 {scalar.get('error_code')!r}"
            f"（{scalar.get('message')!r}）"
        )
        assert "--note" in scalar["message"], f"报错没点出是哪个选项：{scalar['message']!r}"
        assert "标量" in scalar["message"], f"报错没说清参数型别：{scalar['message']!r}"
        unchanged = await self._reload(scalar_task.id)
        assert unchanged is not None

        # ---- ③ 布尔开关：幂等，重复不报错 ----
        bool_result = await self._call(
            f"task ls --assignee {self.ids['worker']} --open_only --open_only"
        )
        assert bool_result["success"] is True, f"布尔开关重复不该报错：{bool_result}"

    async def test_repeated_array_option_two_distinct_values_reach_tool(self):
        """`--status TODO --status DONE`（**两个不同值**）从真入口都到达工具（#218 判据 1）。

        上一条的数组判据用的是两个**相同**值（去重后等于单值，只能证明「不丢」），
        这一条用两个不同值把「聚合」与「只留最后一个」彻底分开：
        旧语义（M1 变异）下只有 `DONE` 会传到 `list_tasks`，TODO 那条任务就查不到。
        """
        done = await self._make_task(status=TaskStatus.DONE, title="重复选项-done")
        todo = await self._make_task(status=TaskStatus.TODO, title="重复选项-todo")
        held = await self._make_task(status=TaskStatus.ON_HOLD, title="重复选项-hold")

        result = await self._call(
            f"task ls --status DONE --status TODO --assignee {self.ids['worker']}"
        )
        assert result["success"] is True, f"console 入口失败：{result}"
        ids = {t["id"] for t in result["tasks"]}
        assert done.id in ids and todo.id in ids, (
            f"两次 --status 未聚合（旧覆盖语义只会命中 DONE），实收 {sorted(ids)}"
        )
        assert held.id not in ids, "ON_HOLD 不该出现在 --status DONE --status TODO 的结果里"

    async def test_alias_and_real_name_count_as_the_same_option(self):
        """别名与真名混写算**同一个**参数重复（#218 判据里的 M4）。

        两个方向各验一次：
        - 数组型混写（`--remove-field` + `--clear_fields`）⇒ 合并进同一个键后聚合，
          解析结果里**只应出现一个键**；按原名分别计数会得到两个键、后一个把前一个
          覆盖掉，聚合静默失效；
        - 标量型混写（`--tz` + `--timezone`）⇒ `duplicate_option`，不许静默取后者。
        """
        from service.funcToolService import consoleTool as ct

        update_spec = consoleRegistry.get_table().by_path["task update"]
        _pos, options, _src = ct.cp._parse_options(
            update_spec,
            ["187", "--remove-field", "reviewer_id", "--clear_fields", "reviewer_id"],
        )
        assert list(options) == ["remove-field"], (
            f"别名与真名应归一到同一个键，实际键集合 {list(options)!r}"
        )
        assert ct._split_csv(options["remove-field"]) == ["reviewer_id"]

        time_spec = consoleRegistry.get_table().by_path["time get"]
        with pytest.raises(ct.ConsoleError) as exc:
            ct.cp._parse_options(time_spec, ["--tz", "UTC", "--timezone", "Asia/Shanghai"])
        assert exc.value.code == "duplicate_option", (
            f"别名混写标量应报 duplicate_option，实际 {exc.value.code!r}"
        )

        # 真入口：`--tz` 与 `--timezone` 混写同样被拒（不是只在解析层单测里成立）。
        via_entry = await self._call("time get --tz UTC --timezone Asia/Shanghai")
        assert via_entry["success"] is False
        assert via_entry["error_code"] == "duplicate_option", (
            f"真入口没拦住别名混写的标量重复：{via_entry}"
        )

    # ==================================================================
    # 裁定③：`--remove-field` 与 reviewer_id 同给 ⇒ 透传服务层报歧义
    # ==================================================================

    async def test_remove_field_with_reviewer_id_is_ambiguous(self):
        """`--remove-field reviewer_id` + `reviewer_id` ⇒ 服务层 `ambiguous_reviewer_change`。

        console 侧**不让它在解析层静默择一**：一个是「改人」一个是「撤销」，
        猜错了就是静默改错人。
        """
        task = await self._make_task(reviewer_id=self.ids["rev_a"])
        result = await self._call(
            f"task update {task.id} --from payload --remove-field reviewer_id",
            payload={"reviewer_id": self.ids["rev_b"]},
        )
        assert result["success"] is False, f"歧义调用竟成功：{result}"
        assert result["error_code"] == "ambiguous_reviewer_change", (
            f"期望 ambiguous_reviewer_change，收到 {result.get('error_code')!r}"
        )
        row = await self._reload(task.id)
        assert row.reviewer_id == self.ids["rev_a"], "被拒绝的调用改了验收人"

    # ==================================================================
    # 正整数校验（裁定⑨）
    # ==================================================================

    @pytest.mark.parametrize("bad", ["abc", "0", "-1", "1.5", ""])
    async def test_non_positive_int_task_id_rejected(self, bad):
        """非正整数的任务 id 在 **console 侧**被挡（`invalid_target`），不查库。"""
        command = f"task get {bad}" if bad else "task get"
        result = await self._call(command)
        assert result["success"] is False, f"非法 id {bad!r} 竟通过：{result}"
        assert result["error_code"] in ("invalid_target", "invalid_argument_count"), (
            f"非法 id {bad!r} 收到的错误码 {result.get('error_code')!r} 不在预期内"
        )

    # ==================================================================
    # 注册与 help 的静态一致性（漏注册的形态）
    # ==================================================================

    def test_task_commands_are_in_the_derived_table(self):
        """task 三条必须真的在 registry 派生出的命令表里（这就是 M3 变异要咬的地方）。"""
        keys = {s.key for s in consoleRegistry.get_table().specs}
        assert {"task ls", "task get", "task update"} <= keys, (
            f"task 命令缺失，当前命令表：{sorted(keys)}"
        )

    def test_task_commands_help_is_available(self):
        """`help task` 与 `help task update` 都能给出详情（否则模型只能瞎试）。"""
        from service.funcToolService.consoleTool import _help_text

        summary = _help_text(["text"])["text"]
        assert "task" in summary
        for action in ("ls", "get", "update"):
            detail = _help_text(["help", "task", action])
            assert detail["success"] is True
            assert f"task {action}" in detail["text"]

    # ==================================================================
    # #222 A6：`task create` 从真入口建单（含唤醒提示透传/追加）
    # ==================================================================

    async def test_task_create_really_creates_row(self):
        """`task create --title ... --assignee_id ...` **真的在库里建出任务**。

        判据必须打到库上：console 返回 `success: True` 只证明「没报错」，若把
        `--title` 吞掉、或把 assignee 丢掉，success 依然为 True。所以这里重读
        行，逐字段比对标题 / 执行人 / 创建人 / 初始状态。
        """
        # 标题不带空格：console 的位置参数里第 1 段会被当成内联 JSON（`accepts_input`），
        # 带空格的标题不引号包裹会被拆成两段。这里顺带把「用 --from payload 传空格标题」
        # 留给下面 `test_task_create_payload_input` 覆盖。
        title = "console建单-真入口-1a2b3c"
        result = await self._call(
            f"task create --title {title} --assignee_id {self.ids['owner']}",
            who="owner",
        )

        assert result["success"] is True, f"建单失败：{result}"
        task_id = result.get("task_id")
        assert isinstance(task_id, int) and task_id > 0, f"返回体没有 task_id：{result}"

        row = await self._reload(task_id)
        assert row.title == title
        # 团队没有部门层级数据时 `create_task` 只允许「分配给自己或直接/间接下属」，
        # 所以这里的执行人用调用者本人（要测「给别人分配」得先建部门树，属 #187 的
        # 权限用例面，不在本单）。
        assert row.assignee_id == self.ids["owner"]
        assert row.creator_id == self.ids["owner"], "创建人必须是调用者"
        assert row.status == TaskStatus.TODO

    async def test_task_create_returns_awake_hint(self):
        """返回体必须带「建单不会自动唤醒执行人」的提示（#222 A6 判据 1）。

        为什么单独立一条：原工具 `create_task` 的返回体**只有**「任务已创建，
        task_id=…」，唤醒提示只写在它的 docstring 里；命令层若不补，模型建完
        单就会干等着，执行人永远不醒。这条同时钉住「补在成功体上」与
        「文案提到 send_chat_msg」。
        """
        result = await self._call(
            f"task create --title 带提示的单 --assignee_id {self.ids['owner']}",
            who="owner",
        )
        assert result["success"] is True, result

        assert "不会自动唤醒" in result["message"], f"message 里没有唤醒提示：{result['message']}"
        assert "send_chat_msg" in result["message"], result["message"]
        assert "不会自动唤醒" in result.get("hint", ""), f"缺 hint 字段：{result}"

    async def test_task_create_payload_input(self):
        """`task create --from payload` 走配置输入（复杂参数推荐路径）也必须能建。"""
        result = await self._call(
            "task create --from payload",
            payload={
                "title": "从 payload 建的单",
                "assignee_id": self.ids["owner"],
                "description": "desc",
                "priority": "HIGH",
            },
            who="owner",
        )
        assert result["success"] is True, result
        row = await self._reload(result["task_id"])
        assert row.title == "从 payload 建的单"
        assert row.priority.value == "HIGH"

    async def test_task_create_without_input_is_rejected(self):
        """裸 `task create` 必须被显式拒绝（不许静默建出空单）。

        P0 起必填项回到签名（设计 §2.2），所以错误码从 console 层自造的
        `missing_input_source` 换成 `missing_required_option`，并点名缺哪几个字段——
        比「你没给输入」更能让模型自纠。
        """
        result = await self._call("task create", who="owner")
        assert result["success"] is False
        assert result["error_code"] == "missing_required_option", result
        assert set(result["missing"]) == {"title", "assignee_id"}, result

    async def test_task_create_is_in_command_index(self):
        """A6 的注册面：`task create` 必须在命令表里（变异删掉这一行即红）。"""
        table = consoleRegistry.get_table()
        assert "task create" in table.by_path
        assert table.by_path["task create"].tool_name == "create_task"

    # ==================================================================
    # #222 A3：`task ls --status TODO` 自动补 `--scope self`（真入口）
    # ==================================================================

    async def test_task_ls_status_only_auto_scopes_to_self(self):
        """`task ls --status TODO` 不再 `missing_target`；结果收窄到「与我相关」且署名。

        三向对照，避免「恒真」：
        - 同一命令下，worker 只看到与自己相关的任务；
        - 显式 `--scope team`（owner 是团队主管，有管理视角）能看到别人的任务；
        - 返回体 `auto_filled_filters` 只在自动补的那一次出现。
        """
        mine = await self._make_task(title="我的待办", status=TaskStatus.TODO)
        others = await self._make_task(title="别人的待办", status=TaskStatus.TODO)
        # 让 others 与 worker 无关：改执行人为 rev_b、验收人置空
        others.assignee_id = self.ids["rev_b"]
        others.reviewer_id = None
        await gtAgentTaskManager.save_task(others, only=[GtAgentTask.assignee_id])

        result = await self._call("task ls --status TODO", who="worker")

        assert result["success"] is True, f"`task ls --status TODO` 仍然失败：{result}"
        assert result["auto_filled_filters"] == {"scope": "self"}, result
        assert "自动补" in result["message"], result["message"]
        ids = {t["id"] for t in result["tasks"]}
        assert mine.id in ids, f"自己的待办没出现：{ids}"
        assert others.id not in ids, f"自动补 scope 没生效，别人的任务也漏出来了：{ids}"

    async def test_task_ls_explicit_scope_not_annotated(self):
        """显式给 `--scope self` 时不许再出现 `auto_filled_filters`（不许伪造补参）。"""
        await self._make_task(title="显式 scope", status=TaskStatus.TODO)
        result = await self._call("task ls --status TODO --scope self", who="worker")
        assert result["success"] is True, result
        assert "auto_filled_filters" not in result, result


class TestConsoleArrayCliNeedFix4(_ConsoleTaskEntryBase):
    """need_fix_4 模块 1：CLI 数组参数按元素类型收敛（B1）、空数组透传（B3）、示例同源（B2）。

    全部走**真 console 入口**（`AgentToolRegistry.execute_tool_call` → 第 2 跳 →
    `taskService` → DB）：上一版三种数组写法之所以全失败，失败点在第 2 跳的 schema
    校验，只测 `_convert_option` 的单元层断言看不到这条链路（need_fix_4 §二 B1）。
    """

    # ---------------- 辅助 ----------------

    async def _create_with(self, client_command: str) -> dict:
        """按「用户会敲的那一行」创建任务（标题按调用累加，避免同名歧义）。"""
        return await self._call(client_command)

    async def _closed_task(self) -> GtAgentTask:
        """先造一个 DONE 任务，供依赖用例引用（依赖未 DONE 会把新任务压成 PENDING）。"""
        task = await self._make_task(title=f"依赖源-{id(self)}", status=TaskStatus.DONE)
        return task

    # ---------------- B1：三种写法等价 ----------------

    async def test_b1_csv_integer_array_writes_db(self):
        """`--depends-on 42,44` 形态：CSV 元素按 schema 收敛成整数并真的落库。"""
        dep_a, dep_b = await self._closed_task(), await self._closed_task()
        result = await self._create_with(
            f"task create --title CSV依赖 --assignee-id {self.ids['owner']} "
            f"--depends-on {dep_a.id},{dep_b.id}"
        )
        assert result["success"] is True, f"CSV 写法仍然失败：{result}"
        task = await self._reload(result["task_id"])
        assert task.depends_on == [dep_a.id, dep_b.id], task.depends_on

    async def test_b1_repeated_option_integer_array_writes_db(self):
        """`--depends-on 42 --depends-on 44` 形态：重复出现按顺序聚合成整数列表。"""
        dep_a, dep_b = await self._closed_task(), await self._closed_task()
        result = await self._create_with(
            f"task create --title 重复依赖 --assignee-id {self.ids['owner']} "
            f"--depends-on {dep_a.id} --depends-on {dep_b.id}"
        )
        assert result["success"] is True, f"重复出现写法仍然失败：{result}"
        task = await self._reload(result["task_id"])
        assert task.depends_on == [dep_a.id, dep_b.id], task.depends_on

    async def test_b1_json_integer_array_writes_db(self):
        """`--depends-on '[42,44]'` 形态：JSON 整数数组不再被「只收字符串」拒掉。"""
        dep_a, dep_b = await self._closed_task(), await self._closed_task()
        result = await self._create_with(
            f"task create --title JSON依赖 --assignee-id {self.ids['owner']} "
            f"--depends-on '[{dep_a.id},{dep_b.id}]'"
        )
        assert result["success"] is True, f"JSON 整数数组仍然失败：{result}"
        task = await self._reload(result["task_id"])
        assert task.depends_on == [dep_a.id, dep_b.id], task.depends_on

    async def test_b1_three_forms_are_equivalent(self):
        """三种写法的落库结果**逐字节等价**（不是「各自能跑通」就算完）。"""
        dep_a = await self._closed_task()
        csv_result = await self._call(
            f"task create --title 等价CSV --assignee-id {self.ids['owner']} --depends-on {dep_a.id}"
        )
        repeated_result = await self._call(
            f"task create --title 等价重复 --assignee-id {self.ids['owner']} "
            f"--depends-on {dep_a.id} --depends-on {dep_a.id}"
        )
        json_result = await self._call(
            f"task create --title 等价JSON --assignee-id {self.ids['owner']} "
            f"--depends-on '[{dep_a.id}]'"
        )
        for result in (csv_result, repeated_result, json_result):
            assert result["success"] is True, result
        deps = [
            (await self._reload(result["task_id"])).depends_on
            for result in (csv_result, repeated_result, json_result)
        ]
        assert deps[0] == deps[1] == deps[2] == [dep_a.id], deps

    async def test_b1_stages_integer_and_object_forms(self):
        """`--stages` 三种写法：整数 CSV、JSON 整数数组、JSON 对象数组都通。"""
        owner = self.ids["rev_a"]
        int_result = await self._call(
            f"task create --title 节点整数 --assignee-id {self.ids['owner']} --stages {owner}"
        )
        assert int_result["success"] is True, f"`--stages {owner}` 失败：{int_result}"
        json_int = await self._call(
            f"task create --title 节点JSON整数 --assignee-id {self.ids['owner']} "
            f"--stages '[{owner}]'"
        )
        assert json_int["success"] is True, f"`--stages '[{owner}]'` 失败：{json_int}"
        json_obj = await self._call(
            f"task create --title 节点JSON对象 --assignee-id {self.ids['owner']} "
            f"--stages '[{{\"owner_id\":{owner}}}]'"
        )
        assert json_obj["success"] is True, f"`--stages '[{{\"owner_id\":{owner}}}]'` 失败：{json_obj}"

        from service import taskService as task_service

        for result in (int_result, json_int, json_obj):
            task = await self._reload(result["task_id"])
            # 节点链真值是 `agent_task_stages` 表（`GtAgentTask` 只有 current_stage 快照）
            nodes = await task_service._load_stages(task)
            assert len(nodes) == 2, f"节点链应为 [执行, {owner}]，实际 {nodes}"
            assert task_service._stage_field(nodes[1], "owner_id") == owner, nodes

    # ---------------- B2：报错示例必须能照着写成功 ----------------

    async def test_b2_error_example_is_executable(self):
        """照报错里的 `example` 抄一遍必须成功——示例错了就是第二个 bug（B2）。"""
        bad = await self._call(
            f"task create --title 示例校验 --assignee-id {self.ids['owner']} --depends-on '[a,b]'"
        )
        assert bad["success"] is False, bad
        assert bad["error_code"] == "invalid_option_value", bad
        example = bad["example"]
        # 示例是「命令 + 选项」整行；把标题换成随机标题后原样执行（选项部分逐字照抄）
        head, _, _tail = example.partition(" --")
        assert head.startswith("task create"), example
        executable = example.replace(head, f"task create --title 示例照抄 --assignee-id {self.ids['owner']}", 1)
        result = await self._call(executable)
        assert result["success"] is True, f"报错示例 `{example}` 照抄仍然失败：{result}"

    async def test_b2_integer_array_error_example_uses_integer_literal(self):
        """整数数组的示例是 `[1,2]` 而不是 `["a","b"]`（示例随元素类型走）。"""
        bad = await self._call(
            f"task create --title 示例类型 --assignee-id {self.ids['owner']} --depends-on '[x]'"
        )
        assert bad["success"] is False, bad
        assert "[1,2]" in bad["example"], bad["example"]
        assert '"a"' not in bad["example"], bad["example"]

    # ---------------- B3：显式 '[]' 与「未提供」分开 ----------------

    async def test_b3_explicit_empty_array_clears_depends_on(self):
        """`task update <id> --depends-on '[]'` **真的清空**依赖（B3 的核心）。"""
        dep = await self._closed_task()
        created = await self._call(
            f"task create --title 清空依赖 --assignee-id {self.ids['owner']} --depends-on {dep.id}"
        )
        assert created["success"] is True, created
        task_id = created["task_id"]
        assert (await self._reload(task_id)).depends_on == [dep.id]

        result = await self._call(f"task update {task_id} --depends-on '[]'")
        assert result["success"] is True, f"`--depends-on '[]'` 被当成未提供：{result}"
        row = await self._reload(task_id)
        assert not row.depends_on, f"依赖没有被清空：{row.depends_on}"

    async def test_b3_allow_skills_empty_means_no_skill(self):
        """`agent create --allow-skills '[]'` = 不授权任何技能（不是「授权全部」）。"""
        result = await self._call(
            "agent create --name noskill_374 --role-template-name console_task_role "
            "--allow-skills '[]'"
        )
        assert result["success"] is True, result
        agents = await gtAgentManager.get_team_all_agents(self.team_id)
        created = next(a for a in agents if a.name == "noskill_374")
        assert created.allow_skills == [], f"`'[]'` 被折成 None（= 全部技能）：{created.allow_skills}"

    async def test_b3_blank_status_still_equals_absent(self):
        """`--status ''` 仍 ≡ 未提供：不许因为「`'[]'` 透传」把空串契约一起改掉（#228）。"""
        task = await self._make_task(title="空串状态", status=TaskStatus.TODO)
        result = await self._call(f"task update {task.id} --status ''")
        assert result["success"] is False, result
        assert result["error_code"] == "no_update_action", result

    async def test_b3_clear_fields_empty_array_still_no_action(self):
        """`--clear-fields '[]'` 仍报 no_update_action（`[]` 在工具层 ≡ 未提供）。"""
        task = await self._make_task(title="空数组清除字段", status=TaskStatus.TODO)
        result = await self._call(f"task update {task.id} --clear-fields '[]'")
        assert result["success"] is False, result
        assert result["error_code"] == "no_update_action", result
