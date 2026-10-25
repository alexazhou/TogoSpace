from __future__ import annotations

import re
from datetime import datetime

import yaml
import service.skillService as skillService
from constants import EVENT_CONTENT_MAX_CHARS, TaskStatus
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
    TODO_TASK_TURN_PROMPT_TEMPLATE,
    TODO_TASK_DONE_INSTRUCTION,
    TODO_TASK_REVIEW_INSTRUCTION,
    LATEST_EVENT_TRUNCATION_HINT_TEMPLATE,
    REVIEW_TASK_TURN_PROMPT_TEMPLATE,
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
        f"```yaml\n{yaml_block}\n```\n\n"
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
        f"```yaml\n{yaml_block}\n```"
    )


def build_compact_instruction(max_tokens: int) -> str:
    return COMPACT_PROMPT_TEMPLATE.format(max_tokens=max_tokens)


def build_compact_resume_prompt(summary: str) -> str:
    return COMPACT_RESUME_TEMPLATE.format(summary=summary.strip())


_EVENT_TRUNCATED_SUFFIX = "…(已截断)"


def format_task_event_content(content: str, max_chars: int = EVENT_CONTENT_MAX_CHARS) -> str:
    """把事件内容压成单行并按共享上限截断（超出部分丢弃并加省略标记）。"""
    text = re.sub(r"\s+", " ", content or "").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + _EVENT_TRUNCATED_SUFFIX


def _status_text(status: TaskStatus | str | None) -> str | None:
    """状态枚举 / 字符串 → 展示用字符串（None 原样返回）。"""
    if status is None:
        return None
    return status.value if isinstance(status, TaskStatus) else str(status)


def _format_event_time(created_at: datetime | None) -> str:
    """事件时间（应用本地时区）格式化为 YYYY-MM-DD HH:MM。"""
    if created_at is None:
        return ""
    if created_at.tzinfo is not None:
        created_at = created_at.astimezone()
    return created_at.strftime("%Y-%m-%d %H:%M")


def build_task_event_line(
    *,
    actor_name: str,
    from_status: TaskStatus | str | None,
    to_status: TaskStatus | str | None,
    content: str,
    created_at: datetime | None = None,
) -> str:
    """把一条任务事件渲染成注入通知的单行文本。

    - 流转事件：``- 2026-10-26 02:41 REVIEWING→IN_PROGRESS（李哥）：打回原因``
    - 评论事件：``- 2026-10-26 02:41 评论（李哥）：备注内容``
    - 创建事件：``- 2026-10-26 02:44 创建为 TODO（系统）``

    内容先压成单行（换行 / 连续空白折叠为一个空格），再按 ``EVENT_CONTENT_MAX_CHARS``
    截断；内容为空时不追加冒号与正文。
    """
    from_text = _status_text(from_status)
    to_text = _status_text(to_status)
    if to_text is None:
        action = "评论"
    elif from_text is None:
        action = f"创建为 {to_text}"
    else:
        action = f"{from_text}→{to_text}"

    time_text = _format_event_time(created_at)
    prefix = f"- {time_text} " if time_text else "- "
    line = f"{prefix}{action}（{actor_name}）"
    body = format_task_event_content(content)
    return f"{line}：{body}" if body else line


def _build_latest_event_block(latest_event: str | None) -> str:
    """把渲染好的最新事件包成通知里的「最近事件」段落；无事件时返回空串（整段不出现）。

    段落内附一句截断口径提示（截断长度直接取共享常量，避免两处口径漂移）。
    """
    if latest_event is None or not latest_event.strip():
        return ""
    hint = LATEST_EVENT_TRUNCATION_HINT_TEMPLATE.format(max_chars=EVENT_CONTENT_MAX_CHARS)
    return f"\n最近事件：\n{latest_event.strip()}\n{hint}\n"


def build_todo_task_turn_prompt(
    title: str,
    description: str,
    status_value: str,
    has_reviewer: bool = False,
    latest_event: str | None = None,
) -> str:
    """构建协作任务（TODO_TASK）turn 的用户提示文本。

    status_value == "REVIEWING" 时为验收通知；否则为执行通知，并按是否设有验收人
    给出互斥的完成方式（有验收人须先 REVIEWING，无验收人直接 DONE）。

    Args:
        latest_event: 由 build_task_event_line 渲染好的「最新一条事件」单行文本（不含标题）；
            为 None / 空串时通知里整段不出现「最近事件」。
    """
    latest_event_block = _build_latest_event_block(latest_event)
    if status_value == "REVIEWING":
        return REVIEW_TASK_TURN_PROMPT_TEMPLATE.format(
            title=title,
            description=description,
            status_value=status_value,
            latest_event_block=latest_event_block,
        )
    complete_instruction = TODO_TASK_REVIEW_INSTRUCTION if has_reviewer else TODO_TASK_DONE_INSTRUCTION
    return TODO_TASK_TURN_PROMPT_TEMPLATE.format(
        title=title,
        description=description,
        status_value=status_value,
        complete_instruction=complete_instruction,
        latest_event_block=latest_event_block,
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
    allow_skills: list[str] | None = None,
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

    # 注入已授权 Skill 概要
    # allow_skills 为 None 表示自动（所有技能），为空列表表示不授权
    if allow_skills is None:
        skills_to_show = skillService.get_all_skills()
    elif allow_skills:
        skills_to_show = [skillService.get_skill(name) for name in allow_skills]
        skills_to_show = [s for s in skills_to_show if s is not None]
    else:
        skills_to_show = []

    if skills_to_show:
        skill_lines = [f"- {s.name}: {s.description}" for s in skills_to_show]
        skill_prompt = (
            "\n\n## 可用技能\n\n"
            "你拥有以下技能，可以调用 load_skill 工具加载详细指引：\n\n"
            + "\n".join(skill_lines)
        )
        full_prompt += skill_prompt

    return full_prompt
