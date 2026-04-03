from constants import SpecialAgent
from dal.db import gtAgentManager, gtDeptManager

_TURN_CONTEXT_SUFFIX = "你现在可以调用工具行动。如果你已完成发言和所有工具调用，请务必调用 finish_chat_turn 结束本轮行动。"


def format_room_message(room_name: str, sender_name: str, content: str) -> str:
    sender_label = "系统提醒" if SpecialAgent.value_of(sender_name) == SpecialAgent.SYSTEM else sender_name
    return f"【房间《{room_name}》】【{sender_label}】： {content}"


def build_turn_context_prompt(room_name: str, message_blocks: list[str]) -> str:
    context = "\n\n".join(message_blocks) if len(message_blocks) > 0 else "(无新消息)"
    return (
        f"【{room_name}】 房间轮到你行动，新消息如下：\n\n"
        f"{context}\n\n"
        f"{_TURN_CONTEXT_SUFFIX}"
    )


async def _build_dept_context(team_id: int, agent_name: str) -> str:
    agent_row = await gtAgentManager.get_agent(team_id, agent_name)
    assert agent_row is not None, f"agent not found: team_id={team_id}, agent_name={agent_name}"

    all_depts = await gtDeptManager.get_all_depts(team_id)
    assert len(all_depts) > 0, f"team has no departments: team_id={team_id}, agent_name={agent_name}"

    agent_dept = None
    for dept in all_depts:
        if agent_row.id in dept.agent_ids:
            agent_dept = dept
            break
    assert agent_dept is not None, f"agent has no department: team_id={team_id}, agent_name={agent_name}"

    dept_id_map = {d.id: d for d in all_depts}
    all_agents = await gtAgentManager.get_team_agents(team_id)
    agent_id_to_name: dict[int, str] = {m.id: m.name for m in all_agents}

    manager_name = agent_id_to_name.get(agent_dept.manager_id, "")
    other_agents = [
        agent_id_to_name[mid]
        for mid in agent_dept.agent_ids
        if mid in agent_id_to_name and agent_id_to_name[mid] != agent_name
    ]

    lines = ["---", "组织信息：", f"- 所在部门：{agent_dept.name}（{agent_dept.responsibility}）"]
    if agent_dept.parent_id is not None:
        parent = dept_id_map.get(agent_dept.parent_id)
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
    template_name: str,
    template_soul: str,
    base_prompt_tmpl: str,
    identity_prompt_tmpl: str,
) -> str:
    identity_prompt = identity_prompt_tmpl.format(agent_name=agent_name, template_name=template_name)
    full_prompt = base_prompt_tmpl + "\n\n" + identity_prompt + "\n\n" + template_soul
    if team_id > 0:
        dept_context = await _build_dept_context(team_id, agent_name)
        full_prompt += "\n\n" + dept_context
    return full_prompt
