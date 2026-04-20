import pytest

from service.agentService import promptBuilder


@pytest.mark.asyncio
async def test_build_agent_system_prompt_includes_team_awareness_guide(monkeypatch):
    async def _build_dept_context(team_id: int, agent_name: str) -> str:
        assert team_id == 1
        assert agent_name == "alice"
        return "---\n组织信息：\n- 所在部门：产品部\n---"

    monkeypatch.setattr(promptBuilder, "_build_dept_context", _build_dept_context)

    result = await promptBuilder.build_agent_system_prompt(
        team_id=1,
        agent_name="alice",
        agent_display_name="Alice",
        template_name="pm",
        template_display_name="PM",
        template_soul="负责推进项目",
        workdir="/workspace/demo",
        base_prompt_tmpl="base prompt",
        identity_prompt_tmpl="我是 {agent_name}，角色 {template_name}\n\n{template_soul}",
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


@pytest.mark.asyncio
async def test_build_agent_system_prompt_skips_team_awareness_when_not_in_team(monkeypatch):
    called = False

    async def _build_dept_context(team_id: int, agent_name: str) -> str:
        nonlocal called
        called = True
        return "should not be used"

    monkeypatch.setattr(promptBuilder, "_build_dept_context", _build_dept_context)

    result = await promptBuilder.build_agent_system_prompt(
        team_id=0,
        agent_name="solo",
        agent_display_name="Solo",
        template_name="helper",
        template_display_name="Helper",
        template_soul="帮助用户完成任务",
        workdir="/workspace/solo",
        base_prompt_tmpl="base prompt",
        identity_prompt_tmpl="我是 {agent_name}，角色 {template_name}\n\n{template_soul}",
    )

    assert called is False
    assert "帮助用户完成任务" in result
    assert "/workspace/solo" in result
    assert "get_dept_info" not in result
    assert "wake_up_agent" not in result
    assert "我是 Solo" in result
    assert "角色 Helper" in result
