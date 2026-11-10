"""入口可达：不 patch 快照，经真实 runner + 真实 history 触发一次 compact。

`test_compact_flow/test.py` 里已有的用例都 patch 了 `_token_snapshot`，
好处是阈值可控，坏处是**「快照接线对不对」这件事没被验到** ——
如果 runner 根本没调 `_token_snapshot()`（例如还在调已删除的
`compact.estimate_tokens`），那些用例照样绿。

这里刻意不 patch 任何 token 相关的东西：

- 真实 `AgentHistoryStore`（真落 SQLite）；
- 真实 `_token_snapshot()` → 真实 `litellm.token_counter` 估算；
- 只有 LLM 请求被打桩（不能让单测发真网络请求）。

于是触发条件由「历史真的长到超过阈值」决定。为了让这件事可控，
把上下文窗口设得极小（context_window=300, reserve=0 → hard=300,
trigger=floor(300*0.85)=255），再塞进远超 255 token 的中文历史。
"""
from __future__ import annotations

import unittest.mock as mock
from unittest.mock import AsyncMock, MagicMock

import pytest

import service.llmService as llmService
import service.ormService as ormService
from constants import AgentActivityStatus, AgentActivityType, AgentHistoryStatus, AgentHistoryTag, DriverType
from model.dbModel.agentMessage import AgentMessage
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtAgentActivity import GtAgentActivity
from model.dbModel.gtAgentHistory import GtAgentHistory
from service.agentService.agentHistoryStore import AgentHistoryStore
from service.agentService.agentTurnRunner import AgentTurnRunner
from service.agentService.driver import AgentDriverConfig
from tests.base import ServiceTestCase
from util import llmApiUtil
from util.llmApiUtil import OpenaiApiRole

_AGENT_ID = 77
_CONFIG_PATCH = "util.configUtil.get_app_config"
_INFER_STREAM_PATCH = "service.agentService.agentTurnRunner.llmService.infer_stream"
_COMPACT_LLM_PATCH = "service.agentService.compact.llmService"

# context_window=300, reserve=0 → hard_limit=300, trigger=floor(300*0.85)=255
_CONTEXT_WINDOW = 300
_HARD_LIMIT = 300
_TRIGGER = 255


def _mock_config():
    from util.configUtil.configTypes import (
        LlmContextConfig, LlmModelConfig, LlmProviderConfig, SettingConfig,
    )
    app_config = MagicMock()
    app_config.setting = SettingConfig(
        version="v2",
        language="zh-CN",
        workspace_root="/tmp/test_workspace",
        default_models={"primary": "mock-model@svc", "lite": "mock-model@svc", "vision": "mock-model@svc"},
        llm_providers=[
            LlmProviderConfig(
                name="svc",
                enable=True,
                type="openai",
                api_key="test-key",
                urls={"openai": "http://127.0.0.1:9999/v1/chat/completions"},
                models=[
                    LlmModelConfig(
                        name="mock-model",
                        protocol="openai",
                        context_config=LlmContextConfig(
                            compact_trigger_ratio=0.85,
                            reserve_output_tokens=0,
                            context_window_tokens=_CONTEXT_WINDOW,
                        ),
                    )
                ],
            )
        ],
    )
    return app_config


def _make_mock_response(content: str):
    msg = llmApiUtil.OpenAIMessage(role=OpenaiApiRole.ASSISTANT, content=content)
    resp = MagicMock()
    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = "stop"
    resp.choices = [choice]
    resp.usage = None
    return resp


class TestCompactReachableWithoutPatchingSnapshot(ServiceTestCase):
    """真实估算驱动的 compact 触发。"""

    @classmethod
    async def async_setup_class(cls):
        await ormService.startup(cls._get_test_db_path())

    @classmethod
    async def async_teardown_class(cls):
        await ormService.shutdown()

    async def _build_long_history(self) -> AgentHistoryStore:
        """构造远超 trigger(255) 的历史；每条内容都足够长，不靠运气。"""
        await GtAgentHistory.delete().aio_execute()
        history = AgentHistoryStore(agent_id=_AGENT_ID)
        for i in range(4):
            await history.append_history_message(GtAgentHistory.build(
                AgentMessage(
                    role=OpenaiApiRole.USER,
                    content=f"第 {i} 轮用户消息：" + "这是一段刻意写得很长的中文内容用于把历史撑过压缩阈值。" * 3,
                ),
                status=AgentHistoryStatus.SUCCESS,
            ))
            await history.append_history_message(GtAgentHistory.build(
                AgentMessage(
                    role=OpenaiApiRole.ASSISTANT,
                    content=f"第 {i} 轮助手回复：" + "这是助手针对上一条消息给出的详细回复内容。" * 3,
                ),
                status=AgentHistoryStatus.SUCCESS,
            ))
        await history.append_history_message(GtAgentHistory.build(
            AgentMessage(role=OpenaiApiRole.USER, content="最新的用户输入"),
            status=AgentHistoryStatus.SUCCESS,
        ))
        return history

    def _make_runner(self, history: AgentHistoryStore) -> AgentTurnRunner:
        gt_agent = GtAgent(
            id=_AGENT_ID, team_id=1, name="ReachBot", role_template_id=1, model="mock-model@svc",
        )
        runner = AgentTurnRunner(
            gt_agent=gt_agent,
            system_prompt="You are a helpful assistant.",
            driver_config=AgentDriverConfig(driver_type=DriverType.NATIVE),
        )
        runner._history = history
        return runner

    async def test_real_estimate_triggers_pre_check_compact(self):
        """历史真实估算超 trigger → pre-check 真的执行了一次 compact。

        判据落在**产物**上，不是「函数被调用过」：
        - history 里出现 `COMPACT_SUMMARY` 标记的行（compact 真的插入成功）；
        - 落库的 COMPACT 活动有 `context_tokens_before` / `context_tokens_after`，
          且 before > after（真的压缩了，不是占位）；
        - assistant 行的 `usage.compact_stage == "pre"`。
        """
        history = await self._build_long_history()
        runner = self._make_runner(history)

        # 先确认前提成立：真实估算确实超过了触发线。
        pre_snapshot = runner._token_snapshot()
        assert pre_snapshot.current_tokens >= _TRIGGER, (
            f"前提不成立：历史只有 {pre_snapshot.current_tokens} token，未达 trigger {_TRIGGER}"
        )

        compact_resp = _make_mock_response("这是压缩摘要：之前讨论了四轮对话。")
        normal_resp = _make_mock_response("好的，我来回答你的最新问题。")

        with (
            mock.patch(_CONFIG_PATCH, return_value=_mock_config()),
            mock.patch(_COMPACT_LLM_PATCH, MagicMock(
                infer_stream=AsyncMock(return_value=llmService.InferResult.success(compact_resp)),
            )),
            mock.patch(_INFER_STREAM_PATCH, AsyncMock(
                return_value=llmService.InferResult.success(normal_resp),
            )),
        ):
            output_item = await history.append_history_init_item(role=OpenaiApiRole.ASSISTANT)
            result = await runner._infer_to_item(output_item, tools=[])

        assert result.content == "好的，我来回答你的最新问题。"

        # 判据 1：compact 真的插入了摘要行
        assert any(
            AgentHistoryTag.COMPACT_SUMMARY in item.tags for item in history
        ), "历史里没有 COMPACT_SUMMARY，compact 未真正执行"

        # 判据 2：COMPACT 活动记录了真实的 before/after，且 before > after
        rows = await GtAgentActivity.select().where(
            (GtAgentActivity.activity_type == AgentActivityType.COMPACT)
            & (GtAgentActivity.agent_id == _AGENT_ID)
        ).aio_execute()
        assert len(rows) == 1, f"应有一条 COMPACT 活动，实际 {len(rows)}"
        assert rows[0].status == AgentActivityStatus.SUCCEEDED
        meta = dict(rows[0].metadata or {})
        assert meta["context_tokens_before"] >= _TRIGGER
        assert meta["context_tokens_after"] < meta["context_tokens_before"], (
            f"压缩后没有变小：before={meta['context_tokens_before']}, "
            f"after={meta['context_tokens_after']}"
        )
        assert meta["context_tokens_after"] < _HARD_LIMIT
        # 实测留档（-s 可见）：before=810 after=79，pre_snapshot=810，trigger=255，hard=300
        print(
            f"\n[evidence] before={meta['context_tokens_before']} "
            f"after={meta['context_tokens_after']} "
            f"pre_snapshot={pre_snapshot.current_tokens} "
            f"trigger={_TRIGGER} hard={_HARD_LIMIT}"
        )

        # 判据 3：assistant 行标了 pre 阶段
        assert output_item.usage is not None
        assert output_item.usage.compact_stage == "pre"

    async def test_no_compact_when_history_below_trigger(self):
        """反向对照：短历史不触发 compact。

        没有这条，上面那条无法区分「真实估算生效」与「无条件压缩」。
        """
        await GtAgentHistory.delete().aio_execute()
        history = AgentHistoryStore(agent_id=_AGENT_ID)
        await history.append_history_message(GtAgentHistory.build(
            AgentMessage(role=OpenaiApiRole.USER, content="短问题"),
            status=AgentHistoryStatus.SUCCESS,
        ))
        runner = self._make_runner(history)

        assert runner._token_snapshot().current_tokens < _TRIGGER

        with (
            mock.patch(_CONFIG_PATCH, return_value=_mock_config()),
            mock.patch(_INFER_STREAM_PATCH, AsyncMock(
                return_value=llmService.InferResult.success(_make_mock_response("短回答")),
            )),
        ):
            output_item = await history.append_history_init_item(role=OpenaiApiRole.ASSISTANT)
            await runner._infer_to_item(output_item, tools=[])

        assert not any(
            AgentHistoryTag.COMPACT_SUMMARY in item.tags for item in history
        ), "短历史不该触发 compact"
        assert output_item.usage is not None
        assert output_item.usage.compact_stage == "none"
