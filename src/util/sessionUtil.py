"""会话标识工具：按 agent + session 首条消息确定性生成 LLM 上游 session id。"""
from __future__ import annotations

import uuid

# 固定命名空间，一旦发布不可修改：修改后所有 agent 的历史 session id 都会变化。
_SESSION_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "togospace/agent-session")


def build_agent_session_id(agent_id: int, first_message_id: int) -> str:
    """生成 agent 当前 session 的 id。

    session 以 compact 边界划分：一次 compact 成功后旧 session 结束，新 session 的首条
    消息是新的 COMPACT_SUMMARY；未发生 compact 时首条消息即对话第一行。

    Args:
        agent_id: Agent 主键。
        first_message_id: 当前 session 首条消息的历史行 id。

    Returns:
        UUIDv5 字符串。同一 (agent_id, first_message_id) 在任何进程、任何重启后都相同。
    """
    return str(uuid.uuid5(_SESSION_NAMESPACE, f"agent:{agent_id}:session:{first_message_id}"))
