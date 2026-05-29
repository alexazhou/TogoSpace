from __future__ import annotations

import yaml
from dal.db import gtAgentManager, gtDeptManager
from model.dbModel.gtRoomMessage import GtRoomMessage
from service.agentService.prompts import (
    TURN_CONTEXT_SUFFIX,
    TEAM_AWARENESS_TOOLS_GUIDE,
    TASK_COLLABORATION_GUIDE,
    ROOT_LEADER_GUIDE,
    COMPACT_PROMPT_TEMPLATE,
    COMPACT_RESUME_TEMPLATE,
    WORKDIR_PROMPT,
    LANGUAGE_CONTEXT_PROMPT,
)
from util import configUtil


class _PromptYamlDumper(yaml.Dumper):
    """YAML Dumper：多行字符串使用 literal block 样式（|），列表项保持缩进。"""

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        return super().increase_indent(flow=flow, indentless=False)

    def _str_representer(dumper: yaml.Dumper, data: str) -> yaml.ScalarNode:
        if "\n" in data:
            return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
        return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_PromptYamlDumper.add_representer(str, _PromptYamlDumper._str_representer)


def _build_yaml_room_block(room_name: str, messages: list[tuple[str, str]]) -> str:
    """将房间名和消息列表序列化为 YAML 块。"""
    msg_data = [{"sender": sender, "content": content} for sender, content in messages]
    return yaml.dump(
        {"roomName": room_name, "messages": msg_data},
        Dumper=_PromptYamlDumper,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    ).strip()


def build_turn_begin_prompt(room_name: str, messages: list[tuple[str, str]]) -> str:
    """构建 turn begin prompt，使用 YAML 格式。

    Args:
        room_name: 房间名称
        messages: 消息列表，每项为 (sender, content) 元组
    """
    yaml_block = _build_yaml_room_block(room_name, messages)
    return (
        f"当前轮到你行动，新消息如下:\n\n"
        f"{yaml_block}\n\n"
        f"{TURN_CONTEXT_SUFFIX}"
    )


def build_turn_begin_prompt_from_messages(
    room_name: str,
    messages: list[GtRoomMessage],
    exclude_agent_id: int,
) -> str:
    """从消息列表构建 turn begin prompt，自动过滤自己的消息。"""
    filtered_messages: list[tuple[str, str]] = []
    for msg in messages:
        if msg.sender_id == exclude_agent_id:
            continue
        filtered_messages.append((msg.sender_display_name, msg.content))
    return build_turn_begin_prompt(room_name, filtered_messages)


def build_turn_update_prompt(
    room_name: str,
    messages: list[GtRoomMessage],
    exclude_agent_id: int,
) -> str:
    """构建 turn update prompt（运行中补充消息），不含 ROOM_TURN_BEGIN 语义，自动过滤自己的消息。

    Args:
        room_name: 房间名称
        messages: 消息列表
        exclude_agent_id: 需要过滤掉的 agent_id（通常为当前 Agent 自己）
    """
    filtered: list[tuple[str, str]] = [
        (msg.sender_display_name, msg.content)
        for msg in messages
        if msg.sender_id != exclude_agent_id
    ]
    yaml_block = _build_yaml_room_block(room_name, filtered)
    return (
        f"房间出现了新的补充信息，请在当前工作过程中参考：\n\n"
        f"{yaml_block}"
    )


def build_compact_instruction(max_tokens: int) -> str:
    return COMPACT_PROMPT_TEMPLATE.format(max_tokens=max_tokens)


def build_compact_resume_prompt(summary: str) -> str:
    return COMPACT_RESUME_TEMPLATE.format(summary=summary.strip())


def build_todo_task_turn_prompt(title: str, description: str, status_value: str) -> str:
    """构建协作任务（TODO_TASK）turn 的用户提示文本。"""
    return (
        f"【任务通知】\n"
        f"你当前被唤醒以处理以下任务：\n"
        f"- 标题: {title}\n"
        f"- 描述: {description}\n"
        f"- 状态: {status_value}\n\n"
        f"请直接开始工作。\n"
        f"- 若完成，请调用 `update_task` 将状态改为 DONE 并填写结果。\n"
        f"- 若需暂缓（本轮无法完成），请调用 `update_task` 将状态改为 ON_HOLD。\n"
        f"- 若需取消，请调用 `update_task` 将状态改为 CANCELLED。\n"
        f"- 无论成败，完成后必须调用 `finish_action`。"
    )


async def _build_dept_context(team_id: int, agent_name: str) -> str:
    gt_agent = await gtAgentManager.get_agent(team_id, agent_name)
    assert gt_agent is not None, f"agent not found: team_id={team_id}, agent_name={agent_name}"

    gt_depts = await gtDeptManager.get_all_depts(team_id)
    assert len(gt_depts) > 0, f"team has no departments: team_id={team_id}, agent_name={agent_name}"

    my_depts = [d for d in gt_depts if gt_agent.id in d.agent_ids]
    assert my_depts, f"agent has no department: team_id={team_id}, agent_name={agent_name}"

    dept_id_map = {d.id: d for d in gt_depts}
    gt_agents = await gtAgentManager.get_team_all_agents(team_id)
    agent_id_to_name: dict[int, str] = {m.id: m.name for m in gt_agents}

    def _dept_full_path(dept_id: int) -> str:
        parts = []
        cur_id: int | None = dept_id
        while cur_id is not None:
            d = dept_id_map.get(cur_id)
            if d is None:
                break
            parts.append(d.name)
            cur_id = d.parent_id
        return " / ".join(reversed(parts))

    dept_entries = []
    for dept in my_depts:
        is_manager = dept.manager_id == gt_agent.id
        entry: dict = {
            "部门": _dept_full_path(dept.id),
            "你在部门中的角色": "主管" if is_manager else "成员",
        }
        if dept.responsibility:
            entry["部门职责"] = dept.responsibility
        if not is_manager:
            manager_name = agent_id_to_name.get(dept.manager_id, "")
            if manager_name:
                entry["本部门主管"] = f"{manager_name}（ID：{dept.manager_id}）"
        other_agents = [
            agent_id_to_name[mid]
            for mid in dept.agent_ids
            if mid in agent_id_to_name and agent_id_to_name[mid] != agent_name
        ]
        if other_agents:
            entry["部门中其他成员"] = other_agents
        dept_entries.append(entry)

    body = yaml.dump(
        {"你在组织中的位置": dept_entries},
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    )
    return f"---\n{body}---"


async def build_agent_system_prompt(
    team_id: int,
    agent_id: int,
    agent_name: str,
    agent_display_name: str,
    template_name: str,
    template_display_name: str,
    template_soul: str,
    workdir: str,
    base_prompt_tmpl: str,
    identity_prompt_tmpl: str,
    is_root_leader: bool = False,
) -> str:
    dept_context = ""
    if team_id > 0:
        dept_context = await _build_dept_context(team_id, agent_name)

    identity_prompt = identity_prompt_tmpl.format(
        agent_id=agent_id,
        agent_name=agent_display_name,
        template_name=template_display_name,
        dept_context=dept_context,
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
        full_prompt += "\n\n" + TEAM_AWARENESS_TOOLS_GUIDE
        full_prompt += "\n\n" + TASK_COLLABORATION_GUIDE
        if is_root_leader:
            full_prompt += "\n\n" + ROOT_LEADER_GUIDE
    return full_prompt
