"""sessionUtil 单元测试：agent session id 的确定性与 session 切换。"""
from __future__ import annotations

import uuid

from util import sessionUtil


def test_same_agent_and_first_message_is_stable():
    first = sessionUtil.build_agent_session_id(42, 100)
    second = sessionUtil.build_agent_session_id(42, 100)

    assert first == second


def test_result_is_valid_uuid():
    session_id = sessionUtil.build_agent_session_id(42, 100)

    assert uuid.UUID(session_id).version == 5


def test_different_agents_produce_different_sessions():
    assert sessionUtil.build_agent_session_id(42, 100) != sessionUtil.build_agent_session_id(43, 100)


def test_new_first_message_produces_new_session():
    old_session = sessionUtil.build_agent_session_id(42, 100)
    new_session = sessionUtil.build_agent_session_id(42, 101)

    assert old_session != new_session
