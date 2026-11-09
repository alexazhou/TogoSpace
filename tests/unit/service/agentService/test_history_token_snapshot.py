"""`AgentHistoryStore` token 口径单元测试。

守的是设计稿 step2_technical.md §5 / §7 的六条规则，每条都刻意设计成
「实现写错了必红」，而不是只跑一遍看有没有抛异常：

1. 无真值锚点 → `current_tokens == estimated_tokens`；
2. 有锚点 → `current_tokens = usage.prompt_tokens + 锚点行起的尾部估算`，
   且 **含锚点行自身**（用具体数字差分断言，不只 `>`）；
3. compact 保留的旧行（id < summary.id）不得当锚点 —— 直接构造
   「summary 在前、旧 usage 行在其后但 id 更小」的形态；
4. pending infer 占位不计入全量估算；
5. `tools` 纳入估算（传/不传结果必须不同，用数百 token 的工具定义造差值）；
6. 六个写操作后缓存必须失效（同一实例上先读一次再改再读）。

估算一律走真实 `litellm.token_counter`（本地 tiktoken，无网络），
不用 mock —— mock 掉估算就无法证明「口径公式」本身对不对。
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from constants import AgentHistoryStatus, AgentHistoryTag, OpenaiApiRole
from model.dbModel.agentMessage import AgentMessage
from model.dbModel.gtAgentHistory import GtAgentHistory
from model.dbModel.historyUsage import HistoryUsage
from service.agentService import agentHistoryStore
from service.agentService.agentHistoryStore import AgentHistoryStore
from util import llmApiUtil

_MODEL = "gpt-4o"
_SYSTEM = "You are a test agent."


def _item(
    *,
    item_id: int,
    seq: int,
    role: OpenaiApiRole,
    content: str | None,
    tags: list[AgentHistoryTag] | None = None,
    usage: HistoryUsage | None = None,
    status: AgentHistoryStatus = AgentHistoryStatus.SUCCESS,
) -> GtAgentHistory:
    """构造一条历史行；`content is None` 表示 pending 占位（无消息体）。"""
    if content is None:
        item = GtAgentHistory.build_placeholder(role=role, status=status, tags=tags, usage=usage)
    else:
        item = GtAgentHistory.build(
            AgentMessage(role=role, content=content), status=status, tags=tags, usage=usage,
        )
    item.id = item_id
    item.seq = seq
    return item


def _usage(prompt_tokens: int, completion_tokens: int = 10) -> HistoryUsage:
    return HistoryUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )


def _msgs_tokens(messages: list[AgentMessage]) -> int:
    """用同一套估算算一份「裸消息列表」的 token，供差分断言使用。"""
    return agentHistoryStore.estimate_messages_tokens(_MODEL, messages)


# ─── 风险点 1：锚点 session 边界（M1 变异点） ──────────────────────────


def test_summary_boundary_excludes_old_usage_row_by_id_not_by_position() -> None:
    """compact 保留的旧行即使排在 summary 之后，也不能当锚点。

    构造的形态（顺序上旧 usage 行在 summary 后面，但 id 更小）：

        _items = [COMPACT_SUMMARY(id=100, seq=0),
                  ASSISTANT(id=50, seq=1, usage=压缩前的 90000),
                  USER(id=51, seq=2)]

    若实现只看「列表位置在 summary 之后」，就会选中 id=50 这行，
    `current_tokens` 直接变成 90000+，红。正确实现应判定「无可用锚点」，
    于是 `current_tokens == estimated_tokens`（几十 token 量级）。
    """
    store = AgentHistoryStore(1, [
        _item(item_id=100, seq=0, role=OpenaiApiRole.USER, content="压缩摘要正文",
              tags=[AgentHistoryTag.COMPACT_SUMMARY]),
        _item(item_id=50, seq=1, role=OpenaiApiRole.ASSISTANT, content="压缩前的旧回复",
              usage=_usage(90000)),
        _item(item_id=51, seq=2, role=OpenaiApiRole.USER, content="新问题"),
    ])

    snapshot = store.token_snapshot(_MODEL, _SYSTEM)

    assert snapshot.accurate_usage is None, (
        f"id=50 < summary.id=100，旧 usage 不该被当成锚点：{snapshot}"
    )
    assert snapshot.accurate_anchor_seq is None
    assert snapshot.current_tokens == snapshot.estimated_tokens
    assert snapshot.current_tokens < 90000


def test_usage_row_after_summary_with_larger_id_is_valid_anchor() -> None:
    """反向对照：id 大于 summary.id 的行**可以**当锚点。

    没有这条，上面那条用例无法区分「边界判据生效」与「干脆不用锚点」。
    """
    store = AgentHistoryStore(1, [
        _item(item_id=100, seq=0, role=OpenaiApiRole.USER, content="压缩摘要正文",
              tags=[AgentHistoryTag.COMPACT_SUMMARY]),
        _item(item_id=200, seq=1, role=OpenaiApiRole.ASSISTANT, content="新 session 的首答",
              usage=_usage(1234)),
    ])

    snapshot = store.token_snapshot(_MODEL, _SYSTEM)

    assert snapshot.accurate_anchor_seq == 1
    assert snapshot.accurate_usage is not None
    assert snapshot.accurate_usage.prompt_tokens == 1234
    assert snapshot.current_tokens > 1234


def test_no_compact_summary_means_any_usage_row_is_anchor() -> None:
    """没有 COMPACT_SUMMARY 时没有 session 边界，最近一条带 usage 的行即锚点。"""
    store = AgentHistoryStore(1, [
        _item(item_id=1, seq=0, role=OpenaiApiRole.USER, content="问题一"),
        _item(item_id=2, seq=1, role=OpenaiApiRole.ASSISTANT, content="答一", usage=_usage(500)),
        _item(item_id=3, seq=2, role=OpenaiApiRole.USER, content="问题二"),
    ])

    snapshot = store.token_snapshot(_MODEL, _SYSTEM)

    assert snapshot.accurate_anchor_seq == 1
    assert snapshot.accurate_usage is not None
    assert snapshot.accurate_usage.prompt_tokens == 500


# ─── 风险点 2：current_tokens 含锚点行自身（M2 变异点） ────────────────


def test_current_tokens_includes_the_anchor_row_itself() -> None:
    """用具体数字差分断言「tail 从锚点行自身开始」。

    anchor.usage.prompt_tokens = 1000，锚点行及之后的消息估算合计为 `tail`。
    期望 `current_tokens == 1000 + tail`。
    若实现从 `anchor_idx + 1` 开始（漏掉锚点行自身），结果会少掉锚点行
    那一条 assistant 消息的 token（本例约 10+），与 `1000 + tail` 不等。
    """
    anchor_reply = AgentMessage(role=OpenaiApiRole.ASSISTANT, content="这是锚点行的回复内容")
    later_user = AgentMessage(role=OpenaiApiRole.USER, content="后续用户问题")
    store = AgentHistoryStore(1, [
        _item(item_id=1, seq=0, role=OpenaiApiRole.USER, content="第一个问题"),
        _item(item_id=2, seq=1, role=OpenaiApiRole.ASSISTANT, content=anchor_reply.content,
              usage=_usage(1000)),
        _item(item_id=3, seq=2, role=OpenaiApiRole.USER, content=later_user.content),
    ])

    tail = _msgs_tokens([anchor_reply, later_user])
    # 差分基线：只算锚点之后的消息
    tail_without_anchor = _msgs_tokens([later_user])

    assert tail > tail_without_anchor, "前提：锚点行自身必须占至少 1 个 token"

    snapshot = store.token_snapshot(_MODEL, _SYSTEM)

    assert snapshot.current_tokens == 1000 + tail
    assert snapshot.current_tokens != 1000 + tail_without_anchor


def test_current_tokens_equals_estimate_when_no_anchor() -> None:
    """无锚点时 `current_tokens` 就是全量估算（不是 0，也不是 None）。"""
    store = AgentHistoryStore(1, [
        _item(item_id=1, seq=0, role=OpenaiApiRole.USER, content="只有用户消息"),
    ])

    snapshot = store.token_snapshot(_MODEL, _SYSTEM)

    assert snapshot.accurate_usage is None
    assert snapshot.current_tokens == snapshot.estimated_tokens
    assert snapshot.estimated_tokens == _msgs_tokens(
        [AgentMessage(role=OpenaiApiRole.USER, content="只有用户消息")]
    ) + _msgs_tokens_system_delta()


def _msgs_tokens_system_delta() -> int:
    """system prompt 单独带来的 token 增量（估算把 system 拼在消息前面）。"""
    with_system = agentHistoryStore.estimate_messages_tokens(
        _MODEL, [AgentMessage(role=OpenaiApiRole.USER, content="只有用户消息")], _SYSTEM,
    )
    without_system = _msgs_tokens([AgentMessage(role=OpenaiApiRole.USER, content="只有用户消息")])
    return with_system - without_system


# ─── 风险点 4：pending infer 占位不计入 ────────────────────────────────


def test_pending_infer_placeholder_not_counted_in_estimate() -> None:
    """尾部 pending infer 占位（无消息体）不计入全量估算。

    判据：带占位与不带占位的估算值**完全相等**；占位若被算进去，
    两者必然不等（哪怕只差几个 token）。
    """
    base_items = [
        _item(item_id=1, seq=0, role=OpenaiApiRole.USER, content="用户问题"),
        _item(item_id=2, seq=1, role=OpenaiApiRole.ASSISTANT, content="助手回复"),
        _item(item_id=3, seq=2, role=OpenaiApiRole.USER, content="下一个问题"),
    ]
    pending = _item(
        item_id=4, seq=3, role=OpenaiApiRole.ASSISTANT, content=None,
        status=AgentHistoryStatus.INIT,
    )

    without_pending = AgentHistoryStore(1, list(base_items)).token_snapshot(_MODEL, _SYSTEM)
    with_pending = AgentHistoryStore(1, list(base_items) + [pending]).token_snapshot(_MODEL, _SYSTEM)

    assert with_pending.estimated_tokens == without_pending.estimated_tokens
    assert with_pending.current_tokens == without_pending.current_tokens


# ─── 风险点 5：tools 纳入估算（M4 变异点） ─────────────────────────────


def _big_tool() -> llmApiUtil.OpenAITool:
    """造一份数百 token 的工具定义，让「传/不传」的差值足够大且稳定。"""
    props = {
        f"param_{i}": {
            "type": "string",
            "description": (
                "A fairly long description for parameter number %d, "
                "used to inflate the token count of this tool schema." % i
            ),
        }
        for i in range(12)
    }
    return llmApiUtil.OpenAITool(
        type="function",
        function=llmApiUtil.OpenAIFunction(
            name="demo_tool",
            description=(
                "This is a demonstration tool with a long description used to verify "
                "that tool schemas contribute to the token estimate."
            ),
            parameters=llmApiUtil.OpenAIFunctionParameter(
                type="object", properties=props, required=list(props),
            ),
        ),
    )


def test_tools_contribute_to_estimate() -> None:
    """传 tools 与不传 tools，估算结果必须不同（差值 > 100 token）。

    若实现漏传 tools（M4 变异），两者相等，红。
    """
    store = AgentHistoryStore(1, [
        _item(item_id=1, seq=0, role=OpenaiApiRole.USER, content="hi"),
    ])
    tool = _big_tool()

    without_tools = store.token_snapshot(_MODEL, _SYSTEM).estimated_tokens
    with_tools = store.token_snapshot(_MODEL, _SYSTEM, tools=[tool]).estimated_tokens

    assert with_tools != without_tools, "tools 未参与估算"
    assert with_tools - without_tools > 100, (
        f"tools 差值只有 {with_tools - without_tools} token，判据不够敏感"
    )


def test_tools_contribute_to_estimate_when_anchor_present() -> None:
    """`estimated_tokens` 始终纳入 tools（即使有锚点，全量估算也不该漏工具定义）。"""
    store = AgentHistoryStore(1, [
        _item(item_id=1, seq=0, role=OpenaiApiRole.ASSISTANT, content="答", usage=_usage(500)),
    ])
    tool = _big_tool()

    without_tools = store.token_snapshot(_MODEL, _SYSTEM).estimated_tokens
    with_tools = store.token_snapshot(_MODEL, _SYSTEM, tools=[tool]).estimated_tokens

    assert with_tools - without_tools > 100


# ─── 风险点 3：缓存失效（M3 变异点） ───────────────────────────────────


@pytest.fixture()
def fake_persistence(monkeypatch):
    """把 history 的写库入口换成 AsyncMock。

    缓存失效判据只关心「写操作有没有让 `_revision` 前进」，与 SQLite 无关；
    不替换的话每个用例都要拉起整套 DB 生命周期，单测变成集成测试。
    """
    monkeypatch.setattr(
        agentHistoryStore.gtAgentHistoryManager, "append_agent_history_message",
        AsyncMock(side_effect=lambda item: item),
    )
    monkeypatch.setattr(
        agentHistoryStore.gtAgentHistoryManager, "insert_agent_history_message_at_seq",
        AsyncMock(side_effect=lambda item: item),
    )
    monkeypatch.setattr(
        agentHistoryStore.gtAgentHistoryManager, "update_agent_history_by_id",
        AsyncMock(return_value=None),
    )


def _cached_estimate(store: AgentHistoryStore, tools=None) -> int:
    """读一次全量估算（第一次调用会填充缓存）。"""
    return store.token_snapshot(_MODEL, _SYSTEM, tools=tools).estimated_tokens


def _assert_cache_invalidated(store: AgentHistoryStore, before: int, label: str) -> None:
    after = _cached_estimate(store)
    assert after != before, f"{label} 之后缓存未失效：仍是 {before}"


@pytest.mark.asyncio
async def test_append_history_message_invalidates_cache(fake_persistence) -> None:
    store = AgentHistoryStore(1, [
        _item(item_id=1, seq=0, role=OpenaiApiRole.USER, content="hi"),
    ])
    before = _cached_estimate(store)

    await store.append_history_message(GtAgentHistory.build(
        AgentMessage(role=OpenaiApiRole.USER, content="这是一条明显更长的新消息，用于改变估算值"),
    ))

    _assert_cache_invalidated(store, before, "append_history_message")


@pytest.mark.asyncio
async def test_finalize_history_item_invalidates_cache(fake_persistence) -> None:
    store = AgentHistoryStore(1, [
        _item(item_id=1, seq=0, role=OpenaiApiRole.ASSISTANT, content=None,
              status=AgentHistoryStatus.INIT),
    ])
    before = _cached_estimate(store)

    await store.finalize_history_item(
        history_id=1,
        message=AgentMessage(role=OpenaiApiRole.ASSISTANT, content="这是补全后的助手回复内容"),
        status=AgentHistoryStatus.SUCCESS,
    )

    _assert_cache_invalidated(store, before, "finalize_history_item")


@pytest.mark.asyncio
async def test_insert_compact_summary_invalidates_cache(fake_persistence) -> None:
    store = AgentHistoryStore(1, [
        _item(item_id=1, seq=0, role=OpenaiApiRole.USER, content="很长的旧消息内容" * 20),
        _item(item_id=2, seq=1, role=OpenaiApiRole.ASSISTANT, content="很长的旧回复" * 20),
    ])
    before = _cached_estimate(store)

    await store.insert_compact_summary(
        AgentMessage(role=OpenaiApiRole.USER, content="摘要"), seq=2,
    )

    _assert_cache_invalidated(store, before, "insert_compact_summary")


def test_replace_invalidates_cache() -> None:
    store = AgentHistoryStore(1, [
        _item(item_id=1, seq=0, role=OpenaiApiRole.USER, content="hi"),
    ])
    before = _cached_estimate(store)

    store.replace([
        _item(item_id=1, seq=0, role=OpenaiApiRole.USER, content="替换成一段完全不同的、更长的消息内容"),
    ])

    _assert_cache_invalidated(store, before, "replace")


@pytest.mark.asyncio
async def test_reload_from_db_invalidates_cache(monkeypatch) -> None:
    store = AgentHistoryStore(1, [
        _item(item_id=1, seq=0, role=OpenaiApiRole.USER, content="hi"),
    ])
    before = _cached_estimate(store)

    async def _fake_reload(agent_id: int):
        return [_item(
            item_id=1, seq=0, role=OpenaiApiRole.USER,
            content="从库里重新加载的一段更长的消息内容，用于改变估算值",
        )]

    monkeypatch.setattr(
        agentHistoryStore.gtAgentHistoryManager, "get_agent_history_after_compact", _fake_reload,
    )

    await store.reload_from_db()

    _assert_cache_invalidated(store, before, "reload_from_db")


def test_invalidate_token_cache_bumps_revision() -> None:
    """视觉兜底回填 recognition 走的是原地修改，只能靠显式失效。

    这里断言的是机制本身：失效后即使消息体没变，也必须重算一次。
    """
    store = AgentHistoryStore(1, [
        _item(item_id=1, seq=0, role=OpenaiApiRole.USER, content="hi"),
    ])
    before = _cached_estimate(store)
    assert store._token_cache is not None

    store.invalidate_token_cache()

    # 缓存被丢弃（下次读取必然重算），但消息体未变 ⇒ 数值相同
    assert store._token_cache is None or store._token_cache[0][3] != before
    after = _cached_estimate(store)
    assert after == before  # 值相同是对的：失效的是缓存，不是内容


def test_cache_hit_returns_same_object_without_recomputing(monkeypatch) -> None:
    """同一 key 重复读取必须命中缓存（否则「带缓存」是空话）。

    判据：第二次读取时估算函数一次都不该被调用。
    """
    store = AgentHistoryStore(1, [
        _item(item_id=1, seq=0, role=OpenaiApiRole.USER, content="hi"),
    ])
    calls = {"n": 0}
    real = agentHistoryStore.estimate_messages_tokens

    def _counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(agentHistoryStore, "estimate_messages_tokens", _counting)

    first = store.token_snapshot(_MODEL, _SYSTEM)
    calls_after_first = calls["n"]
    second = store.token_snapshot(_MODEL, _SYSTEM)

    assert calls_after_first >= 1
    assert calls["n"] == calls_after_first, "第二次读取没有命中缓存"
    assert second is first


def test_cache_key_separates_different_tools() -> None:
    """不同 tools 是不同的口径，不能共用同一份缓存。"""
    store = AgentHistoryStore(1, [
        _item(item_id=1, seq=0, role=OpenaiApiRole.USER, content="hi"),
    ])
    tool = _big_tool()

    without_tools = _cached_estimate(store)
    with_tools = _cached_estimate(store, tools=[tool])
    without_tools_again = _cached_estimate(store)

    assert with_tools != without_tools
    assert without_tools_again == without_tools


def test_cache_key_separates_different_system_prompt() -> None:
    store = AgentHistoryStore(1, [
        _item(item_id=1, seq=0, role=OpenaiApiRole.USER, content="hi"),
    ])

    a = store.token_snapshot(_MODEL, "短").estimated_tokens
    b = store.token_snapshot(_MODEL, "明显更长的一段 system prompt" * 5).estimated_tokens

    assert b > a


# ─── 便捷方法与快照类型 ────────────────────────────────────────────────


def test_estimate_tokens_convenience_matches_snapshot_estimated() -> None:
    store = AgentHistoryStore(1, [
        _item(item_id=1, seq=0, role=OpenaiApiRole.USER, content="hi"),
    ])
    snapshot = store.token_snapshot(_MODEL, _SYSTEM)
    assert store.estimate_tokens(_MODEL, _SYSTEM) == snapshot.estimated_tokens


def test_snapshot_is_frozen() -> None:
    """快照是 frozen dataclass：口径值不允许被调用方就地改写。"""
    store = AgentHistoryStore(1, [
        _item(item_id=1, seq=0, role=OpenaiApiRole.USER, content="hi"),
    ])
    snapshot = store.token_snapshot(_MODEL, _SYSTEM)
    with pytest.raises(Exception):
        snapshot.current_tokens = 1  # type: ignore[misc]


def test_accurate_usage_prompt_cache_usage_is_none() -> None:
    """§8.3 决策 A：从 history 还原的 usage 没有 cache 信息，恒为 None。"""
    store = AgentHistoryStore(1, [
        _item(item_id=1, seq=0, role=OpenaiApiRole.ASSISTANT, content="答", usage=_usage(100)),
    ])
    snapshot = store.token_snapshot(_MODEL, _SYSTEM)
    assert snapshot.accurate_usage is not None
    assert snapshot.accurate_usage.prompt_cache_usage is None


def test_usage_without_prompt_tokens_is_not_anchor() -> None:
    """`usage.prompt_tokens is None` 的行不能当锚点（缺真值）。"""
    store = AgentHistoryStore(1, [
        _item(item_id=1, seq=0, role=OpenaiApiRole.ASSISTANT, content="答",
              usage=HistoryUsage(completion_tokens=5, total_tokens=5)),
    ])
    snapshot = store.token_snapshot(_MODEL, _SYSTEM)
    assert snapshot.accurate_usage is None
    assert snapshot.current_tokens == snapshot.estimated_tokens
