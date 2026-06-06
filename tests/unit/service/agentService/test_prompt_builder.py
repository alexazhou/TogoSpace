import pytest

from datetime import datetime

from model.dbModel.gtRoomMessage import GtRoomMessage
from service.agentService import promptBuilder


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
    result = promptBuilder.build_todo_task_turn_prompt("写周报", "整理本周进展", "IN_PROGRESS")
    assert "你当前被唤醒以处理以下任务" in result
    assert "请直接开始工作" in result
    assert "验收不通过" not in result
    assert "ON_HOLD" in result
    assert "CANCELLED" in result


def test_build_todo_task_turn_prompt_for_review_task():
    result = promptBuilder.build_todo_task_turn_prompt("验收功能", "检查交付结果", "REVIEWING")
    assert "你当前被唤醒以处理以下验收任务" in result
    assert "请直接开始验收" in result
    assert "验收不通过" in result
    assert "IN_PROGRESS" in result
    assert "其他少见情况" in result



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
async def test_build_agent_system_prompt_no_skill_when_allow_skills_none(monkeypatch):
    """allow_skills 为 None 时，不注入 Skill 概要。"""

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
        allow_skills=None,
    )

    assert "可用技能" not in result


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
