from __future__ import annotations

from constants import SpecialAgent
from dal.db import gtAgentManager, gtDeptManager
from model.coreModel.gtCoreChatModel import GtCoreRoomMessage
from service.agentService.prompts import (
    TURN_CONTEXT_SUFFIX,
    TEAM_AWARENESS_TOOLS_GUIDE,
    COMPACT_PROMPT_TEMPLATE,
    COMPACT_RESUME_TEMPLATE,
    WORKDIR_PROMPT,
    LANGUAGE_CONTEXT_PROMPT,
)
from util import configUtil


def format_room_message(room_name: str, sender_name: str, content: str) -> str:
    sender_label = "系统提醒" if SpecialAgent.value_of(sender_name) == SpecialAgent.SYSTEM else sender_name
    return f"【房间《{room_name}》】【{sender_label}】： {content}"


def build_turn_begin_prompt(room_name: str, message_blocks: list[str]) -> str:
    context = "\n\n".join(message_blocks) if len(message_blocks) > 0 else "(无新消息)"
    return (
        f"当前轮到你行动，房间名:【{room_name}】,新消息如下:\n\n"
        f"{context}\n\n"
        f"{TURN_CONTEXT_SUFFIX}"
    )


def build_turn_begin_prompt_from_messages(
    room_name: str,
    messages: list[GtCoreRoomMessage],
    exclude_agent_id: int,
) -> str:
    """从消息列表构建 turn begin prompt，自动过滤自己的消息并格式化。"""
    message_blocks: list[str] = []
    for msg in messages:
        if msg.sender_id == exclude_agent_id:
            continue
        message_blocks.append(format_room_message(room_name, msg.sender_name, msg.content))
    return build_turn_begin_prompt(room_name, message_blocks)


def build_compact_instruction(max_tokens: int) -> str:
    return COMPACT_PROMPT_TEMPLATE.format(max_tokens=max_tokens)


def build_compact_resume_prompt(summary: str) -> str:
    return COMPACT_RESUME_TEMPLATE.format(summary=summary.strip())


async def _build_dept_context(team_id: int, agent_name: str) -> str:
    gt_agent = await gtAgentManager.get_agent(team_id, agent_name)
    assert gt_agent is not None, f"agent not found: team_id={team_id}, agent_name={agent_name}"

    gt_depts = await gtDeptManager.get_all_depts(team_id)
    assert len(gt_depts) > 0, f"team has no departments: team_id={team_id}, agent_name={agent_name}"

    gt_dept = None
    for item in gt_depts:
        if gt_agent.id in item.agent_ids:
            gt_dept = item
            break
    assert gt_dept is not None, f"agent has no department: team_id={team_id}, agent_name={agent_name}"

    dept_id_map = {d.id: d for d in gt_depts}
    gt_agents = await gtAgentManager.get_team_agents(team_id)
    agent_id_to_name: dict[int, str] = {m.id: m.name for m in gt_agents}

    manager_name = agent_id_to_name.get(gt_dept.manager_id, "")
    other_agents = [
        agent_id_to_name[mid]
        for mid in gt_dept.agent_ids
        if mid in agent_id_to_name and agent_id_to_name[mid] != agent_name
    ]

    lines = ["---", "组织信息：", f"- 所在部门：{gt_dept.name}（{gt_dept.responsibility}）"]
    if gt_dept.parent_id is not None:
        parent = dept_id_map.get(gt_dept.parent_id)
        if parent is not None:
            parent_manager = agent_id_to_name.get(parent.manager_id, "")
            lines.append(f"- 上级部门：{parent.name}（主管：{parent_manager}）")

    if len(manager_name) > 0 and manager_name != agent_name:
        lines.append(f"- 本部门主管：{manager_name}")
    if len(other_agents) > 0:
        lines.append(f"- 本部门其他成员：{', '.join(other_agents)}")

    lines.append("---")
    return "\n".join(lines)


async def build_agent_system_prompt(
    team_id: int,
    agent_name: str,
    agent_display_name: str,
    template_name: str,
    template_display_name: str,
    template_soul: str,
    workdir: str,
    base_prompt_tmpl: str,
    identity_prompt_tmpl: str,
) -> str:
    identity_prompt = identity_prompt_tmpl.format(
        agent_name=agent_display_name,
        template_name=template_display_name,
        template_soul=template_soul,
    )
    workdir_prompt = WORKDIR_PROMPT.format(workdir=workdir)
    language_context_prompt = LANGUAGE_CONTEXT_PROMPT.format(language=configUtil.get_language())
    full_prompt = (
        base_prompt_tmpl
        + "\n\n"
        + language_context_prompt
        + "\n\n"
        + identity_prompt
        + "\n\n"
        + workdir_prompt
    )
    if team_id > 0:
        dept_context = await _build_dept_context(team_id, agent_name)
        full_prompt += "\n\n" + dept_context
        full_prompt += "\n\n" + TEAM_AWARENESS_TOOLS_GUIDE
    return full_prompt
