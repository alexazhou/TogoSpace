import pytest

from datetime import datetime, timezone

from constants import EVENT_CONTENT_MAX_CHARS, EVENT_TRUNCATED_SUFFIX, TaskStatus
from model.dbModel.gtRoomMessage import GtRoomMessage
from service.agentService import promptBuilder


# 任务通知里的任务 ID（断言用固定值，避免测试与真实 ID 耦合）
_TASK_ID = 7


def _msg(agent_id: int, display_name: str, content: str, *, insert_immediately: bool = False) -> GtRoomMessage:
    m = GtRoomMessage(room_id=1, sender_id=agent_id, content=content,
                      send_time=datetime(2024, 1, 1), insert_immediately=insert_immediately)
    m.sender_display_name = display_name
    return m


# ─── build_turn_update_prompt ────────────────────────────────


def test_build_turn_update_prompt_contains_intro_text():
    result = promptBuilder.build_turn_update_prompt("testRoom", [_msg(2, "Alice", "hello")], exclude_agent_id=1)
    assert "房间出现了新的补充信息" in result


def test_build_turn_update_prompt_yaml_format():
    result = promptBuilder.build_turn_update_prompt("testRoom", [_msg(2, "Alice", "hello")], exclude_agent_id=1)
    assert "roomName: testRoom" in result
    assert "sender: Alice" in result
    assert "content: hello" in result


def test_build_turn_update_prompt_filters_own_messages():
    msgs = [_msg(1, "Self", "own msg"), _msg(2, "Alice", "their msg")]
    result = promptBuilder.build_turn_update_prompt("testRoom", msgs, exclude_agent_id=1)
    assert "own msg" not in result
    assert "their msg" in result


def test_build_turn_update_prompt_empty_messages_after_filter():
    """只有自己的消息时，过滤后 messages 为空列表。"""
    result = promptBuilder.build_turn_update_prompt("testRoom", [_msg(1, "Self", "x")], exclude_agent_id=1)
    assert "messages: []" in result


def test_build_turn_update_prompt_multiline_content_uses_block_style():
    msgs = [_msg(2, "Bob", "line1\nline2\nline3")]
    result = promptBuilder.build_turn_update_prompt("testRoom", msgs, exclude_agent_id=1)
    assert "line1" in result
    assert "line2" in result
    assert "line3" in result
    # 多行内容应使用 YAML 块样式，而非转义换行符
    assert "\\n" not in result


def test_build_turn_begin_prompt_yaml_format():
    result = promptBuilder.build_turn_begin_prompt("myRoom", [("Charlie", "hi there")])
    assert "roomName: myRoom" in result
    assert "sender: Charlie" in result
    assert "content: hi there" in result
    assert "当前轮到你行动" in result


def test_build_todo_task_turn_prompt_for_normal_task():
    """无验收人：完成路径直接 DONE，不出现 REVIEWING。"""
    result = promptBuilder.build_todo_task_turn_prompt(_TASK_ID, "写周报", "整理本周进展", "IN_PROGRESS")
    assert "你当前被唤醒以处理以下任务" in result
    # 任务 ID 必须注入，Agent 才能直接 update_task / get_task_detail，不必先反查
    assert f"- 任务 ID: {_TASK_ID}" in result
    assert "请直接开始工作" in result
    assert "验收不通过" not in result
    assert "ON_HOLD" in result
    assert "改为 DONE，并在 `note` 中写明结果" in result
    assert "REVIEWING" not in result
    # 旧签名 result 参数不得再出现在文案中
    assert "result" not in result


def test_build_todo_task_turn_prompt_with_reviewer_requires_reviewing():
    """有验收人：完成路径须先 REVIEWING，与无验收人文案互斥。"""
    result = promptBuilder.build_todo_task_turn_prompt(_TASK_ID, "写周报", "整理本周进展", "IN_PROGRESS", has_reviewer=True)
    assert "你当前被唤醒以处理以下任务" in result
    assert "请直接开始工作" in result
    assert "改为 REVIEWING 提交验收" in result
    # 不得出现「执行人直接 DONE」的无验收人文案
    assert "将状态改为 DONE，并在 `note` 中写明结果" not in result
    assert "ON_HOLD" in result
    assert "result" not in result


# ─── 取消权限：只有创建人/验收人能取消（执行人通常两者都不是）─────────────


def test_task_turn_prompt_without_cancel_permission_has_no_cancel_command():
    """默认（无取消权限）不得给出「请调用 update_task 改为 CANCELLED」这种必然被拒的指令。

    执行通知的收件人是执行人，而取消权限属于创建人与验收人
    （`taskService.update_task` 的 `→CANCELLED` 分支）。照旧文案去调用会拿到
    `permission_denied`，随后 `finish_action` 又被拒（任务仍非终态），
    重试耗尽后 Agent 直接被打成 FAILED——所以这条指令必须按权限分支。
    """
    result = promptBuilder.build_todo_task_turn_prompt(_TASK_ID, "写周报", "整理进展", "IN_PROGRESS")

    assert "CANCELLED" not in result
    assert "你无权自行取消任务" in result
    # 给出可执行的退路：说明原因只是外部动作，必须落到 ON_HOLD 才能通过 finish_action
    # （任务仍为 TODO/IN_PROGRESS 时 finish_action 会被拒），否则模型会卡在重试里。
    assert "将状态改为 ON_HOLD" in result
    assert "请创建人或验收人取消" in result


def test_task_turn_prompt_with_cancel_permission_offers_cancel_command():
    """自己创建的任务（creator == assignee）才给出取消指令。"""
    result = promptBuilder.build_todo_task_turn_prompt(
        _TASK_ID, "写周报", "整理进展", "IN_PROGRESS", can_cancel=True)

    assert "请调用 `update_task` 将状态改为 CANCELLED" in result
    assert "你无权自行取消任务" not in result


def test_task_turn_prompt_cancel_instruction_is_mutually_exclusive():
    """两种文案互斥：任何组合下都不会同时出现「可取消」与「无权取消」。"""
    for has_reviewer in (False, True):
        for can_cancel in (False, True):
            result = promptBuilder.build_todo_task_turn_prompt(
                _TASK_ID, "写周报", "整理进展", "IN_PROGRESS",
                has_reviewer=has_reviewer, can_cancel=can_cancel)
            has_command = "请调用 `update_task` 将状态改为 CANCELLED" in result
            has_denial = "你无权自行取消任务" in result
            assert has_command is can_cancel
            assert has_denial is (not can_cancel)


def test_build_todo_task_turn_prompt_for_review_task():
    result = promptBuilder.build_todo_task_turn_prompt(_TASK_ID, "验收功能", "检查交付结果", "REVIEWING")
    assert "你当前被唤醒以处理以下验收任务" in result
    assert f"- 任务 ID: {_TASK_ID}" in result
    assert "请直接开始验收" in result
    assert "验收不通过" in result
    assert "IN_PROGRESS" in result
    assert "其他少见情况" in result
    assert "改为 DONE，并在 `note` 中写明结果" in result
    assert "并在 `note` 中写明打回原因" in result
    assert "result" not in result
    # REVIEWING 的出边只有 DONE / IN_PROGRESS / CANCELLED，不能转 ON_HOLD
    # （`taskService._VALID_TRANSITIONS`），给出这条指令只会换来 invalid_transition
    assert "ON_HOLD" not in result
    assert "CANCELLED" in result
    # 验收人必然有权取消，故不受 can_cancel 影响
    assert "你无权自行取消任务" not in result
    # 验收通知不受 has_reviewer 影响（验收人必然存在）
    assert result == promptBuilder.build_todo_task_turn_prompt(_TASK_ID, "验收功能", "检查交付结果", "REVIEWING", has_reviewer=True)
    # 未注入事件时不应出现「最近事件」段
    assert "最近事件" not in result


# ─── 任务通知注入「最近事件」 ─────────────────────────────────


_EVENT_LINE = "- 2026-10-26 02:41 REVIEWING→IN_PROGRESS（李哥）：加密逻辑未覆盖空口令用例"


def test_task_turn_prompt_without_latest_event_has_no_event_section():
    """三种通知在未传 latest_event 时都不出现「最近事件」段（向后兼容）。"""
    without_reviewer = promptBuilder.build_todo_task_turn_prompt(_TASK_ID, "写周报", "整理进展", "IN_PROGRESS")
    with_reviewer = promptBuilder.build_todo_task_turn_prompt(_TASK_ID, "写周报", "整理进展", "IN_PROGRESS", has_reviewer=True)
    review = promptBuilder.build_todo_task_turn_prompt(_TASK_ID, "验收功能", "检查交付结果", "REVIEWING")

    for result in (without_reviewer, with_reviewer, review):
        assert "最近事件" not in result


def test_task_turn_prompt_with_latest_event_in_all_variants():
    """三种通知都带上「最近事件」段与事件原文。"""
    without_reviewer = promptBuilder.build_todo_task_turn_prompt(
        _TASK_ID, "写周报", "整理进展", "IN_PROGRESS", latest_event=_EVENT_LINE)
    with_reviewer = promptBuilder.build_todo_task_turn_prompt(
        _TASK_ID, "写周报", "整理进展", "IN_PROGRESS", has_reviewer=True, latest_event=_EVENT_LINE)
    review = promptBuilder.build_todo_task_turn_prompt(
        _TASK_ID, "验收功能", "检查交付结果", "REVIEWING", latest_event=_EVENT_LINE)

    for result in (without_reviewer, with_reviewer, review):
        assert "最近事件：" in result
        assert _EVENT_LINE in result
        # 截断口径提示（含共享上限值 + 指向 get_task_event）
        assert f"事件内容超过 {EVENT_CONTENT_MAX_CHARS} 字会被截断" in result
        assert "`get_task_event`" in result
        # 段落位置：在状态行之后、行动指引之前
        assert result.index("最近事件：") > result.index("- 状态: ")
        assert result.index("最近事件：") < result.index("无论成败")


def test_task_turn_prompt_blank_latest_event_hides_section():
    """latest_event 为空串 / 空白串时整段不出现（不留空标题）。"""
    for blank in ("", "   ", "\n"):
        result = promptBuilder.build_todo_task_turn_prompt(_TASK_ID, "写周报", "整理进展", "IN_PROGRESS", latest_event=blank)
        assert "最近事件" not in result
        assert "get_task_event" not in result
        assert result == promptBuilder.build_todo_task_turn_prompt(_TASK_ID, "写周报", "整理进展", "IN_PROGRESS")


def test_build_task_event_line_transition_format():
    """流转事件：`- YYYY-MM-DD HH:MM FROM→TO（actor）：content`。"""
    line = promptBuilder.build_task_event_line(
        actor_name="李哥",
        from_status=TaskStatus.REVIEWING,
        to_status=TaskStatus.IN_PROGRESS,
        content="加密逻辑未覆盖空口令用例",
        created_at=datetime(2026, 10, 26, 2, 41),
    )
    assert line == _EVENT_LINE


def test_build_task_event_line_comment_format():
    """评论事件（to_status 为空）：`- YYYY-MM-DD HH:MM 评论（actor）：content`。"""
    line = promptBuilder.build_task_event_line(
        actor_name="小马哥",
        from_status=None,
        to_status=None,
        content="补充：注意兼容旧数据",
        created_at=datetime(2026, 10, 26, 3, 5),
    )
    assert line == "- 2026-10-26 03:05 评论（小马哥）：补充：注意兼容旧数据"


def test_build_task_event_line_creation_event_without_content():
    """创建事件 content 为空时不出现裸冒号。"""
    line = promptBuilder.build_task_event_line(
        actor_name="系统",
        from_status=None,
        to_status=TaskStatus.TODO,
        content="",
        created_at=datetime(2026, 10, 26, 2, 44),
    )
    assert line == "- 2026-10-26 02:44 创建为 TODO（系统）"
    assert "：" not in line


def test_build_task_event_line_collapses_multiline_content():
    """多行 note 折叠成单行（保证注入文本真是一行）。"""
    line = promptBuilder.build_task_event_line(
        actor_name="李哥",
        from_status="IN_PROGRESS",
        to_status="REVIEWING",
        content="F4 完成，请验收。\n\n【改动清单】\n1.\tsrc/dal/db/gtAgentTaskManager.py  ",
        created_at=datetime(2026, 10, 26, 2, 35),
    )
    assert line == "- 2026-10-26 02:35 IN_PROGRESS→REVIEWING（李哥）：F4 完成，请验收。 【改动清单】 1. src/dal/db/gtAgentTaskManager.py"
    assert "\n" not in line


def test_build_task_event_line_truncates_long_content():
    """单条 content 超过共享上限即截断并加省略标记。"""
    long_content = "长" * (EVENT_CONTENT_MAX_CHARS + 50)
    line = promptBuilder.build_task_event_line(
        actor_name="李哥",
        from_status="IN_PROGRESS",
        to_status="REVIEWING",
        content=long_content,
        created_at=datetime(2026, 10, 26, 4, 0),
    )
    assert line.startswith("- 2026-10-26 04:00 IN_PROGRESS→REVIEWING（李哥）：")
    assert line.endswith(EVENT_TRUNCATED_SUFFIX)
    body = line.split("：", 1)[1]
    assert body == "长" * EVENT_CONTENT_MAX_CHARS + EVENT_TRUNCATED_SUFFIX
    # 边界：恰好等于上限时不截断
    exact = promptBuilder.format_task_event_content("字" * EVENT_CONTENT_MAX_CHARS)
    assert exact == "字" * EVENT_CONTENT_MAX_CHARS
    assert EVENT_TRUNCATED_SUFFIX not in exact


def test_build_task_event_line_time_uses_local_timezone():
    """带时区的时间按应用本地时区展示。"""
    aware = datetime(2026, 10, 26, 2, 41, tzinfo=timezone.utc)
    line = promptBuilder.build_task_event_line(
        actor_name="李哥",
        from_status="TODO",
        to_status="IN_PROGRESS",
        content="开工",
        created_at=aware,
    )
    expected_time = aware.astimezone().strftime("%Y-%m-%d %H:%M")
    assert line == f"- {expected_time} TODO→IN_PROGRESS（李哥）：开工"
    # 无时间时不留多余空格
    assert promptBuilder.build_task_event_line(
        actor_name="李哥", from_status="TODO", to_status="IN_PROGRESS", content="开工", created_at=None
    ) == "- TODO→IN_PROGRESS（李哥）：开工"


def test_task_collaboration_guide_lists_get_task_event():
    """协作指南需包含按事件 id 查单条事件的工具 get_task_event。"""
    from service.agentService.prompts import TASK_COLLABORATION_GUIDE

    assert "`get_task_event`" in TASK_COLLABORATION_GUIDE


def test_task_collaboration_guide_does_not_promise_unsupported_status_forms():
    """系统提示**不得**写「可按状态或状态数组」「大小写不敏感」这类做不到的形态。

    `claude_sdk` 侧 `status` 是 `array` + 7 个大写 enum，单值 `"TODO"` 会被 schema 拒掉
    （实测 `{"status": "TODO"}` → REJECT: 'TODO' is not of type 'array'）。
    提示词对三种驱动都可见（`promptBuilder.py` 在 `team_id > 0` 时无条件拼接，
    `claudeSdkDriver` 用的就是 `host.system_prompt`），写进去模型就会照着传。

    过滤参数的细节统一由**工具 schema/description** 承载（见 `tools.list_tasks` 的
    docstring 与 `test_task_tools.py`），提示词只写一句「按条件搜索」。
    """
    from service.agentService.prompts import TASK_COLLABORATION_GUIDE

    assert "状态或状态数组" not in TASK_COLLABORATION_GUIDE
    assert "可按状态、" not in TASK_COLLABORATION_GUIDE
    assert "大小写不敏感" not in TASK_COLLABORATION_GUIDE
    assert "不区分大小写" not in TASK_COLLABORATION_GUIDE


def test_task_collaboration_guide_does_not_enumerate_list_tasks_filters():
    """提示词里**不再枚举** `list_tasks` 的过滤参数。

    枚举过参数的旧版本有两个代价：一是每加/改一个参数都要同步改提示词（否则文案与
    真实签名脱节，本次改动的起因就是 `name_keyword` 的语义写反了）；二是这类细节在
    schema 里已经完整给出，重复一遍只是噪声。

    所以这里钉住「不枚举」：一旦有人把参数名写回提示词，本用例必红，提醒他确认
    是否真的需要重复 schema 里的信息。
    """
    from service.agentService.prompts import TASK_COLLABORATION_GUIDE

    line = next(l for l in TASK_COLLABORATION_GUIDE.splitlines() if "list_tasks" in l)

    assert "按条件搜索" in line
    for param in ("assignee", "reviewer", "status", "title_keyword", "open_only", "limit", "scope"):
        assert f"`{param}`" not in line, f"提示词不应枚举过滤参数 `{param}`（细节由工具 schema 承载）"


def test_task_collaboration_guide_referenced_tools_all_exist():
    """提示词里所有反引号工具名都必须在工具注册表里（防改名后提示词悄悄失效）。

    正则必须含数字：工具名可能带版本后缀（`query_tasks_v2`），只用 `[a-z_]+` 会把
    `_v2` 截断成 `query_tasks`，改坏时测试照样绿（本护栏第一版就踩过这个坑）。
    """
    import re

    from service.agentService.prompts import TASK_COLLABORATION_GUIDE, TEAM_AWARENESS_TOOLS_GUIDE
    from service.funcToolService.core import load_func_tools

    registry = set(load_func_tools().keys())
    # 反引号里也会出现**参数**名（不是工具），逐个列白名单而不是放宽容错
    param_names = {"new_status", "note", "status", "assignee", "reviewer", "title_keyword"}

    text = TEAM_AWARENESS_TOOLS_GUIDE + "\n" + TASK_COLLABORATION_GUIDE
    referenced = {
        n for n in re.findall(r"`([a-z][a-z0-9_]*)`", text)
        if n not in param_names
    }

    assert referenced, "至少应解析出若干工具名，否则护栏本身失效"
    assert referenced <= registry, f"提示词引用了不存在的工具: {sorted(referenced - registry)}"
    # 护栏有效性自检：带数字的工具名必须能被正则抓到
    assert "list_tasks" in referenced


@pytest.mark.asyncio
async def test_build_agent_system_prompt_includes_team_awareness_guide(monkeypatch):
    async def _build_dept_context(team_id: int, agent_name: str) -> str:
        assert team_id == 1
        assert agent_name == "alice"
        return "---\n组织信息：\n- 所在部门：产品部\n---"

    monkeypatch.setattr(promptBuilder, "_build_dept_context", _build_dept_context)
    monkeypatch.setattr(promptBuilder.configUtil, "get_language", lambda: "en")

    result = await promptBuilder.build_agent_system_prompt(
        team_id=1,
        agent_id=1,
        agent_name="alice",
        agent_display_name="Alice",
        template_name="pm",
        template_display_name="PM",
        template_soul="负责推进项目",
        workdir="/workspace/demo",
        base_prompt_tmpl="base prompt",
        identity_prompt_tmpl="我是 {agent_name}，角色 {template_name}\n\n{dept_context}\n\n{template_soul}",
    )

    assert "组织信息" in result
    assert "负责推进项目" in result
    assert "/workspace/demo" in result
    assert "get_dept_info" in result
    assert "get_room_info" in result
    assert "get_agent_info" in result
    assert "wake_up_agent" in result
    assert "我是 Alice" in result
    assert "角色 PM" in result
    assert "当前系统语言设置：en" in result
    assert "上一条 Agent/Operator 消息的正文语言" in result
    assert "系统通知不参与语言判断" in result


@pytest.mark.asyncio
async def test_build_agent_system_prompt_skips_team_awareness_when_not_in_team(monkeypatch):
    called = False

    async def _build_dept_context(team_id: int, agent_name: str) -> str:
        nonlocal called
        called = True
        return "should not be used"

    monkeypatch.setattr(promptBuilder, "_build_dept_context", _build_dept_context)
    monkeypatch.setattr(promptBuilder.configUtil, "get_language", lambda: "zh-CN")

    result = await promptBuilder.build_agent_system_prompt(
        team_id=0,
        agent_id=2,
        agent_name="solo",
        agent_display_name="Solo",
        template_name="helper",
        template_display_name="Helper",
        template_soul="帮助用户完成任务",
        workdir="/workspace/solo",
        base_prompt_tmpl="base prompt",
        identity_prompt_tmpl="我是 {agent_name}，角色 {template_name}\n\n{dept_context}\n\n{template_soul}",
    )

    assert called is False
    assert "帮助用户完成任务" in result
    assert "/workspace/solo" in result
    assert "get_dept_info" not in result
    assert "wake_up_agent" not in result
    assert "我是 Solo" in result
    assert "角色 Helper" in result
    assert "当前系统语言设置：zh-CN" in result
    assert "如果上一条 Agent/Operator 消息不存在，则使用当前系统语言设置" in result


# ─── Skill 概要注入 tests ─────────────────────────────────────


@pytest.mark.asyncio
async def test_build_agent_system_prompt_includes_skill_summary(monkeypatch):
    """allow_skills 非空时，system prompt 末尾包含 Skill 概要。"""

    async def _build_dept_context(team_id: int, agent_name: str) -> str:
        return "---\n组织信息：\n- 所在部门：测试部\n---"

    monkeypatch.setattr(promptBuilder, "_build_dept_context", _build_dept_context)
    monkeypatch.setattr(promptBuilder.configUtil, "get_language", lambda: "zh-CN")

    # Mock skillService.get_skill
    mock_skill_info = type("SkillInfo", (), {
        "name": "code_review",
        "description": "代码审查技能包",
    })()
    monkeypatch.setattr(
        promptBuilder.skillService,
        "get_skill",
        lambda name: mock_skill_info if name == "code_review" else None,
    )

    result = await promptBuilder.build_agent_system_prompt(
        team_id=1,
        agent_id=1,
        agent_name="alice",
        agent_display_name="Alice",
        template_name="pm",
        template_display_name="PM",
        template_soul="负责推进项目",
        workdir="/workspace/demo",
        base_prompt_tmpl="base prompt",
        identity_prompt_tmpl="我是 {agent_name}，角色 {template_name}\n\n{dept_context}\n\n{template_soul}",
        allow_skills=["code_review"],
    )

    assert "可用技能" in result
    assert "load_skill" in result
    assert "code_review" in result
    assert "代码审查技能包" in result


@pytest.mark.asyncio
async def test_build_agent_system_prompt_all_skills_when_allow_skills_none(monkeypatch):
    """allow_skills 为 None 时，自动注入所有技能。"""

    async def _build_dept_context(team_id: int, agent_name: str) -> str:
        return "---\n组织信息：\n- 所在部门：测试部\n---"

    monkeypatch.setattr(promptBuilder, "_build_dept_context", _build_dept_context)
    monkeypatch.setattr(promptBuilder.configUtil, "get_language", lambda: "zh-CN")

    mock_skill_info = type("SkillInfo", (), {
        "name": "any_skill",
        "description": "任意技能",
    })()
    monkeypatch.setattr(
        promptBuilder.skillService,
        "get_all_skills",
        lambda: [mock_skill_info],
    )

    result = await promptBuilder.build_agent_system_prompt(
        team_id=1,
        agent_id=1,
        agent_name="alice",
        agent_display_name="Alice",
        template_name="pm",
        template_display_name="PM",
        template_soul="负责推进项目",
        workdir="/workspace/demo",
        base_prompt_tmpl="base prompt",
        identity_prompt_tmpl="我是 {agent_name}，角色 {template_name}\n\n{dept_context}\n\n{template_soul}",
        allow_skills=None,
    )

    assert "可用技能" in result


@pytest.mark.asyncio
async def test_build_agent_system_prompt_no_skill_when_allow_skills_empty(monkeypatch):
    """allow_skills 为空列表时，不注入 Skill 概要。"""

    async def _build_dept_context(team_id: int, agent_name: str) -> str:
        return "---\n组织信息：\n- 所在部门：测试部\n---"

    monkeypatch.setattr(promptBuilder, "_build_dept_context", _build_dept_context)
    monkeypatch.setattr(promptBuilder.configUtil, "get_language", lambda: "zh-CN")

    result = await promptBuilder.build_agent_system_prompt(
        team_id=1,
        agent_id=1,
        agent_name="alice",
        agent_display_name="Alice",
        template_name="pm",
        template_display_name="PM",
        template_soul="负责推进项目",
        workdir="/workspace/demo",
        base_prompt_tmpl="base prompt",
        identity_prompt_tmpl="我是 {agent_name}，角色 {template_name}\n\n{dept_context}\n\n{template_soul}",
        allow_skills=[],
    )

    assert "可用技能" not in result


@pytest.mark.asyncio
async def test_build_agent_system_prompt_skill_not_found_in_registry(monkeypatch):
    """allow_skills 中的某个 Skill 在注册表中不存在时，跳过该 Skill。"""

    async def _build_dept_context(team_id: int, agent_name: str) -> str:
        return "---\n组织信息：\n- 所在部门：测试部\n---"

    monkeypatch.setattr(promptBuilder, "_build_dept_context", _build_dept_context)
    monkeypatch.setattr(promptBuilder.configUtil, "get_language", lambda: "zh-CN")

    # get_skill 总返回 None → 所有 Skill 名都不在注册表中
    monkeypatch.setattr(promptBuilder.skillService, "get_skill", lambda name: None)

    result = await promptBuilder.build_agent_system_prompt(
        team_id=1,
        agent_id=1,
        agent_name="alice",
        agent_display_name="Alice",
        template_name="pm",
        template_display_name="PM",
        template_soul="负责推进项目",
        workdir="/workspace/demo",
        base_prompt_tmpl="base prompt",
        identity_prompt_tmpl="我是 {agent_name}，角色 {template_name}\n\n{dept_context}\n\n{template_soul}",
        allow_skills=["nonexistent_skill"],
    )

    # 全部 Skill 都找不到，不注入概要
    assert "可用技能" not in result
