from __future__ import annotations

import re
from datetime import datetime

import service.deptService as deptService
import service.skillService as skillService
from constants import EVENT_CONTENT_MAX_CHARS, EVENT_TRUNCATED_SUFFIX, TaskStatus
from dal.db import gtAgentManager
from model.dbModel.gtRoomMessage import GtRoomMessage
from service.agentService.promptRenderer import render_prompt
from service.agentService.prompts import (
    BASE_PROMPT,
    TURN_CONTEXT_SUFFIX,
)
from util import configUtil


def build_turn_begin_prompt(room_name: str, messages: list[tuple[str, str, datetime | None]]) -> str:
    """构建 turn begin prompt，使用 YAML 格式。

    Args:
        room_name: 房间名称
        messages: 消息列表，每项为 (sender, content, send_time) 元组
    """
    msg_data = [
        {"sender": sender, "sendTime": _format_time(send_time), "content": content}
        for sender, content, send_time in messages
    ]
    return render_prompt(
        "turn_begin.j2",
        room_name=room_name,
        messages=msg_data,
        turn_context_suffix=TURN_CONTEXT_SUFFIX,
    )


def build_turn_begin_prompt_from_messages(
    room_name: str,
    messages: list[GtRoomMessage],
    exclude_agent_id: int,
) -> str:
    """从消息列表构建 turn begin prompt，自动过滤自己的消息。"""
    filtered_messages: list[tuple[str, str, datetime | None]] = []
    for msg in messages:
        if msg.sender_id == exclude_agent_id:
            continue
        filtered_messages.append((msg.sender_display_name, msg.content, msg.send_time))
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
    filtered = [
        {"sender": msg.sender_display_name, "sendTime": _format_time(msg.send_time), "content": msg.content}
        for msg in messages
        if msg.sender_id != exclude_agent_id
    ]
    return render_prompt("turn_update.j2", room_name=room_name, messages=filtered)


def build_compact_instruction(max_tokens: int) -> str:
    return render_prompt("compact.j2", max_tokens=max_tokens)


def build_compact_resume_prompt(summary: str) -> str:
    return render_prompt("compact_resume.j2", summary=summary.strip())


def format_task_event_content(content: str, max_chars: int = EVENT_CONTENT_MAX_CHARS) -> str:
    """把事件内容压成单行并按共享上限截断（超出部分丢弃并加省略标记）。"""
    text = re.sub(r"\s+", " ", content or "").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + EVENT_TRUNCATED_SUFFIX


def _status_text(status: TaskStatus | str | None) -> str | None:
    """状态枚举 / 字符串 → 展示用字符串（None 原样返回）。"""
    if status is None:
        return None
    return status.value if isinstance(status, TaskStatus) else str(status)


def _format_time(value: datetime | None) -> str:
    """时间（应用本地时区）格式化为 YYYY-MM-DD HH:MM。"""
    if value is None:
        return ""
    if value.tzinfo is not None:
        value = value.astimezone()
    return value.strftime("%Y-%m-%d %H:%M")


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

    return render_prompt(
        "task_event_line.j2",
        time_text=_format_time(created_at),
        action=action,
        actor_name=actor_name,
        body=format_task_event_content(content),
    )


def build_todo_task_turn_prompt(
    task_id: int,
    title: str,
    description: str,
    status_value: str,
    has_reviewer: bool = False,
    can_cancel: bool = False,
    latest_event: str | list[str] | None = None,
) -> str:
    """构建协作任务（TODO_TASK）turn 的用户提示文本。

    status_value == "REVIEWING" 时为验收通知；否则为执行通知，并按是否设有验收人
    给出互斥的完成方式（有验收人须先 REVIEWING，无验收人直接 DONE）。

    Args:
        task_id: 协作任务 ID。注入通知是为了让 Agent 直接调用 `update_task` /
            `get_task_detail`，不必先 `list_tasks` 反查自己的任务。
        can_cancel: 收件人是否有权取消该任务。取消权限属于创建人与验收人
            （`taskService.update_task`），而执行通知的收件人通常两者都不是，
            故默认 False——不给必然被 `permission_denied` 拒绝的指令。
        latest_event: 最近事件文本（单条或列表）。传 None 或空时通知里整段不出现「最近事件」。
    """
    if isinstance(latest_event, str):
        events = [latest_event.strip()] if latest_event.strip() else []
    elif isinstance(latest_event, list):
        events = [e.strip() for e in latest_event if e and e.strip()]
    else:
        events = []

    context = dict(
        task_id=task_id,
        title=title,
        description=description,
        status_value=status_value,
        has_reviewer=has_reviewer,
        can_cancel=can_cancel,
        events=events,
        max_chars=EVENT_CONTENT_MAX_CHARS,
    )
    if status_value == "REVIEWING":
        return render_prompt("review_task_turn.j2", **context)
    return render_prompt("todo_task_turn.j2", **context)


async def _build_dept_context(team_id: int, agent_name: str, agent_id: int | None = None) -> list[dict] | str:
    """获取 Agent 所在部门结构。未 mock 时返回结构化字典列表，由模板用 to_yaml 序列化。"""
    if agent_id is None:
        gt_agent = await gtAgentManager.get_agent(team_id, agent_name)
        assert gt_agent is not None, f"agent not found: team_id={team_id}, agent_name={agent_name}"
        agent_id = gt_agent.id

    agent_depts = await deptService.get_agent_depts(team_id, agent_id)
    if not agent_depts:
        return []

    gt_agents = await gtAgentManager.get_team_all_agents(team_id)
    agent_names = {m.id: m.name for m in gt_agents}

    entries = []
    for dept, path in agent_depts:
        is_manager = dept.manager_id == agent_id
        entry: dict = {
            "部门": path,
            "你在部门中的角色": "主管" if is_manager else "成员",
        }
        if dept.responsibility:
            entry["部门职责"] = dept.responsibility
        if not is_manager and dept.manager_id in agent_names:
            entry["本部门主管"] = f"{agent_names[dept.manager_id]}（ID：{dept.manager_id}）"
        others = [agent_names[mid] for mid in dept.agent_ids if mid in agent_names and mid != agent_id]
        if others:
            entry["部门中其他成员"] = others
        entries.append(entry)
    return entries


async def build_agent_system_prompt(
    team_id: int,
    agent_id: int,
    agent_name: str,
    agent_display_name: str,
    template_name: str,
    template_display_name: str,
    template_soul: str,
    workdir: str,
    shared_workdir: str | None = None,
    is_root_leader: bool = False,
    allow_skills: list[str] | None = None,
    base_prompt_tmpl: str | None = None,
    identity_prompt_tmpl: str | None = None,
    system_prompt_tmpl: str | None = None,
) -> str:
    """构建 Agent 系统级提示词（System Prompt）。

    纯数据装配层：拉取部门组织数据与已授权技能，统一委托给 system_prompt.j2 模板渲染。
    """
    dept_entries: list[dict] = []
    dept_context: str = ""
    if team_id > 0:
        try:
            res = await _build_dept_context(team_id, agent_name, agent_id=agent_id)
        except TypeError:
            res = await _build_dept_context(team_id, agent_name)

        if isinstance(res, list):
            dept_entries = res
        elif isinstance(res, str):
            dept_context = res

    skills_to_show = []
    if allow_skills is None:
        skills_to_show = skillService.get_all_skills()
    elif allow_skills:
        skills_to_show = [skillService.get_skill(name) for name in allow_skills]
        skills_to_show = [s for s in skills_to_show if s is not None]

    # 兼容测试传入的自定义模板（未传入时完全由模板原生渲染）
    custom_identity = ""
    if identity_prompt_tmpl:
        custom_identity = identity_prompt_tmpl.format(
            agent_id=agent_id,
            agent_name=agent_display_name,
            template_name=template_display_name,
            dept_context=dept_context,
            template_soul=template_soul,
        ).strip()

    custom_base_prompt = ""
    if base_prompt_tmpl and base_prompt_tmpl.strip() != BASE_PROMPT.strip():
        custom_base_prompt = base_prompt_tmpl.strip()

    is_leader = is_root_leader or any(entry.get("你在部门中的角色") == "主管" for entry in dept_entries)

    return render_prompt(
        "system_prompt.j2",
        base_prompt=custom_base_prompt,
        identity_prompt=custom_identity,
        language=configUtil.get_language(),
        agent_id=agent_id,
        agent_name=agent_display_name,
        template_name=template_display_name,
        dept_entries=dept_entries,
        dept_context=dept_context,
        template_soul=template_soul,
        workdir=workdir,
        shared_workdir=shared_workdir,
        is_leader=is_leader,
        is_root_leader=is_root_leader,
        skills=skills_to_show,
    )
