"""`memoryService` 单元测试（Agent 长期记忆系统，#134 段1）。

**用真实 SQLite（跑过 0020 迁移）而不是 mock DAL**：
本模块的核心难点是「同名防重 + 星标配额 + 写入」这一整段必须在同一事务里、
且配额统计口径是「除本条外」，这些都是 SQL 语义（`exclude_id`、`status=ACTIVE`
过滤、`Case` 排序）。mock 掉 DAL 就只能验证「我调用了几次」，验证不到真正的行为。
`atomic()` 也因此能跑在真实事务上。

覆盖 step2 §7.1 的十条 error_code（九条原文 + PM 裁定 F 增补的
`invalid_memory_query`），见文件末尾的对照表。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from constants import (
    MEMORY_CONTENT_MAX_CHARS,
    MEMORY_SEARCH_LIMIT_MAX,
    MEMORY_STAR_SUMMARY_LIMIT,
    MEMORY_STAR_TOTAL_LIMIT,
    MEMORY_SUMMARY_MAX_CHARS,
    MEMORY_TAG_MAX_CHARS,
    MEMORY_TAGS_MAX_COUNT,
    MEMORY_TITLE_MAX_CHARS,
    MemoryStarLevel,
    MemoryStatus,
)
from exception import TogoException
from model.dbModel.base import bind_database, unbind_database
from model.dbModel.gtAgentMemory import GtAgentMemory
from service import memoryService
from service.ormService import AioSqliteDatabase

_MIGRATION_SQL = (
    Path(__file__).resolve().parents[3] / "assets" / "migrate" / "0020_add_agent_memories.sql"
)

_TEAM_ID = 2
_AGENT_ID = 7


@pytest.fixture
async def memory_db(tmp_path):
    assert _MIGRATION_SQL.exists(), f"迁移文件不存在：{_MIGRATION_SQL}"

    db_path = tmp_path / "agent_memories.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(_MIGRATION_SQL.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()

    db = AioSqliteDatabase(str(db_path), pool_params={"timeout": 30})
    bind_database(db)
    await db.aio_connect()
    try:
        yield db
    finally:
        await db.aio_close()
        unbind_database()


async def _create(title: str, **kwargs):
    """建一条记忆的快捷方式（默认参数只为省字数，各用例按需覆盖）。"""
    params = {
        "team_id": _TEAM_ID,
        "agent_id": _AGENT_ID,
        "title": title,
        "summary": f"{title} 的概要",
        "content": f"{title} 的正文",
    }
    params.update(kwargs)
    return await memoryService.create_memory(**params)


async def _expect_error(error_code: str, coro):
    """断言协程抛 `TogoException` 且 `error_code` 匹配，返回异常本身。"""
    with pytest.raises(TogoException) as excinfo:
        await coro
    assert excinfo.value.error_code == error_code, (
        f"期望 error_code={error_code}，实际 {excinfo.value.error_code}；message={excinfo.value}"
    )
    return excinfo.value


# ---------------------------------------------------------------------------
# 枚举解析
# ---------------------------------------------------------------------------


def test_enum_values_are_lowercase_strings():
    """枚举 value 必须是固定小写字符串（step2 §2.2：保留小写 value，不用 auto()）。"""
    assert MemoryStarLevel.NONE.value == "none"
    assert MemoryStarLevel.TITLE.value == "title"
    assert MemoryStarLevel.SUMMARY.value == "summary"
    assert MemoryStatus.ACTIVE.value == "active"
    assert MemoryStatus.ARCHIVED.value == "archived"
    # 不能是 int（auto() 会让 `.value` 变成 3，序列化出错误契约）
    for member in list(MemoryStarLevel) + list(MemoryStatus):
        assert isinstance(member.value, str), f"{member} 的 value 不是字符串：{member.value!r}"


def test_parse_star_level_accepts_name_and_value():
    """工具入参大小写不敏感，name（SUMMARY）与 value（summary）都接受。"""
    assert memoryService.parse_star_level("summary", default=MemoryStarLevel.NONE) is MemoryStarLevel.SUMMARY
    assert memoryService.parse_star_level("SUMMARY", default=MemoryStarLevel.NONE) is MemoryStarLevel.SUMMARY
    assert memoryService.parse_star_level(None, default=MemoryStarLevel.TITLE) is MemoryStarLevel.TITLE
    assert memoryService.parse_star_level("", default=MemoryStarLevel.TITLE) is MemoryStarLevel.TITLE

    with pytest.raises(TogoException):
        memoryService.parse_star_level("gold", default=MemoryStarLevel.NONE)


def test_parse_status_accepts_name_and_value():
    assert memoryService.parse_status("archived", default=MemoryStatus.ACTIVE) is MemoryStatus.ARCHIVED
    assert memoryService.parse_status("ARCHIVED", default=MemoryStatus.ACTIVE) is MemoryStatus.ARCHIVED
    with pytest.raises(TogoException):
        memoryService.parse_status("deleted", default=MemoryStatus.ACTIVE)


# ---------------------------------------------------------------------------
# error_code ①：invalid_memory_title
# ---------------------------------------------------------------------------


async def test_invalid_memory_title_empty(memory_db):
    await _expect_error("invalid_memory_title", _create("   "))


async def test_invalid_memory_title_too_long(memory_db):
    await _expect_error("invalid_memory_title", _create("题" * (MEMORY_TITLE_MAX_CHARS + 1)))


async def test_invalid_memory_title_with_newline(memory_db):
    await _expect_error("invalid_memory_title", _create("主题\n第二行"))


async def test_title_at_limit_is_accepted(memory_db):
    """边界：正好 80 字符必须通过（上限是 ≤，不是 <）。"""
    detail = await _create("题" * MEMORY_TITLE_MAX_CHARS)
    assert len(detail.title) == MEMORY_TITLE_MAX_CHARS


# ---------------------------------------------------------------------------
# error_code ②：duplicate_memory_title
# ---------------------------------------------------------------------------


async def test_duplicate_memory_title_reports_existing_id(memory_db):
    """同名活跃标题被拦，且 message 里带已有记忆的 `#id`（引导 Agent 去 update）。"""
    existing = await _create("方案输出规范")

    exc = await _expect_error("duplicate_memory_title", _create("方案输出规范"))
    assert f"#{existing.id}" in exc.error_message


async def test_duplicate_memory_title_check_is_agent_scoped(memory_db):
    """同名判定按 Agent 隔离：别的 Agent 有同名记忆不影响本 Agent。"""
    await _create("同名主题", agent_id=8)
    detail = await _create("同名主题")
    assert detail.title == "同名主题"


async def test_duplicate_title_allowed_after_archive(memory_db):
    """已归档的同名记忆允许新建（step2 §7.2 明写）。"""
    archived = await _create("旧方案")
    await memoryService.update_memory(
        _TEAM_ID, _AGENT_ID, archived.id, status="archived",
    )

    recreated = await _create("旧方案")
    assert recreated.id != archived.id
    assert recreated.status is MemoryStatus.ACTIVE


async def test_update_title_to_existing_active_rejected(memory_db):
    """改名撞到别人的活跃标题也要拦，且 message 带已有 id。"""
    first = await _create("主题A")
    second = await _create("主题B")

    exc = await _expect_error(
        "duplicate_memory_title",
        memoryService.update_memory(_TEAM_ID, _AGENT_ID, second.id, title="主题A"),
    )
    assert f"#{first.id}" in exc.error_message


async def test_update_same_title_does_not_self_conflict(memory_db):
    """标题不变只改概要：不能把自己判成重名（`exclude_id` 生效）。"""
    detail = await _create("不变标题")
    updated = await memoryService.update_memory(
        _TEAM_ID, _AGENT_ID, detail.id, title="不变标题", summary="新的概要",
    )
    assert updated.summary == "新的概要"


# ---------------------------------------------------------------------------
# error_code ③：invalid_memory_summary
# ---------------------------------------------------------------------------


async def test_invalid_memory_summary_empty(memory_db):
    await _expect_error("invalid_memory_summary", _create("标题", summary="  "))


async def test_invalid_memory_summary_too_long(memory_db):
    await _expect_error(
        "invalid_memory_summary", _create("标题", summary="概" * (MEMORY_SUMMARY_MAX_CHARS + 1)),
    )


async def test_invalid_memory_summary_with_newline(memory_db):
    await _expect_error("invalid_memory_summary", _create("标题", summary="第一句\n第二句"))


async def test_update_invalid_summary(memory_db):
    detail = await _create("标题")
    await _expect_error(
        "invalid_memory_summary",
        memoryService.update_memory(
            _TEAM_ID, _AGENT_ID, detail.id, summary="概" * (MEMORY_SUMMARY_MAX_CHARS + 1),
        ),
    )


# ---------------------------------------------------------------------------
# error_code ④：summary_required
# ---------------------------------------------------------------------------


async def test_summary_required_when_content_changed_without_summary(memory_db):
    detail = await _create("标题")
    await _expect_error(
        "summary_required",
        memoryService.update_memory(_TEAM_ID, _AGENT_ID, detail.id, content="新的正文"),
    )


async def test_summary_required_when_summary_blank_with_content(memory_db):
    """传了 content 且 summary 传空串：按 `summary_required` 拒绝（不是 empty summary）。"""
    detail = await _create("标题")
    await _expect_error(
        "summary_required",
        memoryService.update_memory(
            _TEAM_ID, _AGENT_ID, detail.id, content="新的正文", summary="   ",
        ),
    )


async def test_content_and_summary_together_is_accepted(memory_db):
    detail = await _create("标题")
    updated = await memoryService.update_memory(
        _TEAM_ID, _AGENT_ID, detail.id, content="新的正文", summary="新的概要",
    )
    assert updated.content == "新的正文"
    assert updated.summary == "新的概要"


async def test_update_summary_only_allowed(memory_db):
    """只改概要（不动正文）不需要 content，必须放行。"""
    detail = await _create("标题")
    updated = await memoryService.update_memory(
        _TEAM_ID, _AGENT_ID, detail.id, summary="只改概要",
    )
    assert updated.summary == "只改概要"
    assert updated.content == detail.content


# ---------------------------------------------------------------------------
# error_code ⑤：content_too_long
# ---------------------------------------------------------------------------


async def test_content_too_long(memory_db):
    await _expect_error(
        "content_too_long", _create("标题", content="正" * (MEMORY_CONTENT_MAX_CHARS + 1)),
    )


async def test_content_empty(memory_db):
    """正文为空按 step2 §7.1 同码处理（文档把「为空或超长」合并在 `content_too_long`）。"""
    await _expect_error("content_too_long", _create("标题", content="   "))


async def test_content_at_limit_is_accepted(memory_db):
    detail = await _create("标题", content="正" * MEMORY_CONTENT_MAX_CHARS)
    assert len(detail.content) == MEMORY_CONTENT_MAX_CHARS


async def test_content_allows_newlines(memory_db):
    """正文允许换行（代码块 / 分步说明），只有标题与摘要禁用换行。"""
    detail = await _create("标题", content="步骤1\n步骤2\n步骤3")
    assert "\n" in detail.content


# ---------------------------------------------------------------------------
# error_code ⑥：invalid_memory_tags
# ---------------------------------------------------------------------------


async def test_invalid_memory_tags_too_many(memory_db):
    tags = [f"标签{i}" for i in range(MEMORY_TAGS_MAX_COUNT + 1)]
    await _expect_error("invalid_memory_tags", _create("标题", tags=tags))


async def test_invalid_memory_tags_too_long(memory_db):
    await _expect_error(
        "invalid_memory_tags", _create("标题", tags=["标" * (MEMORY_TAG_MAX_CHARS + 1)]),
    )


async def test_tags_at_limit_is_accepted(memory_db):
    tags = [f"标签{i}" for i in range(MEMORY_TAGS_MAX_COUNT)]
    detail = await _create("标题", tags=tags)
    assert detail.tags == tags


async def test_tags_blank_entries_dropped(memory_db):
    """空白标签被丢弃，不占数量配额。"""
    detail = await _create("标题", tags=["  交付规范  ", "", "  "])
    assert detail.tags == ["交付规范"]


# ---------------------------------------------------------------------------
# error_code ⑦ / ⑧：star_limit_exceeded、summary_star_limit_exceeded
# ---------------------------------------------------------------------------


async def test_star_limit_exceeded(memory_db):
    """活跃星标总数上限 12：第 13 条被拒绝。"""
    for index in range(MEMORY_STAR_TOTAL_LIMIT):
        await _create(f"星标{index}", star_level=MemoryStarLevel.TITLE)

    await _expect_error(
        "star_limit_exceeded", _create("超限星标", star_level=MemoryStarLevel.TITLE),
    )


async def test_star_limit_counts_title_and_summary_together(memory_db):
    """总上限是 TITLE + SUMMARY 合计，不是各自 12。"""
    for index in range(MEMORY_STAR_SUMMARY_LIMIT):
        await _create(f"概要星标{index}", star_level=MemoryStarLevel.SUMMARY)
    for index in range(MEMORY_STAR_TOTAL_LIMIT - MEMORY_STAR_SUMMARY_LIMIT):
        await _create(f"标题星标{index}", star_level=MemoryStarLevel.TITLE)

    await _expect_error(
        "star_limit_exceeded", _create("再一条", star_level=MemoryStarLevel.TITLE),
    )


async def test_summary_star_limit_exceeded(memory_db):
    """概要级上限 6：第 7 条 `summary` 被拒绝（即使总数还没到 12）。"""
    for index in range(MEMORY_STAR_SUMMARY_LIMIT):
        await _create(f"概要{index}", star_level=MemoryStarLevel.SUMMARY)

    await _expect_error(
        "summary_star_limit_exceeded", _create("超限概要", star_level=MemoryStarLevel.SUMMARY),
    )


async def test_title_still_allowed_when_summary_full(memory_db):
    """概要满了只是不能再加 summary，title 仍可加到总上限。"""
    for index in range(MEMORY_STAR_SUMMARY_LIMIT):
        await _create(f"概要{index}", star_level=MemoryStarLevel.SUMMARY)

    detail = await _create("标题星标", star_level=MemoryStarLevel.TITLE)
    assert detail.star_level is MemoryStarLevel.TITLE


async def test_none_level_never_hits_quota(memory_db):
    """普通记忆不受星标配额限制。"""
    for index in range(MEMORY_STAR_TOTAL_LIMIT):
        await _create(f"星标{index}", star_level=MemoryStarLevel.TITLE)
    for index in range(5):
        await _create(f"普通{index}", star_level=MemoryStarLevel.NONE)


async def test_none_to_summary_upgrade_hits_summary_limit(memory_db):
    """`none -> summary` 升级超限要拦（step2 §7.2 明写覆盖此路径）。"""
    for index in range(MEMORY_STAR_SUMMARY_LIMIT):
        await _create(f"概要{index}", star_level=MemoryStarLevel.SUMMARY)
    plain = await _create("普通记忆", star_level=MemoryStarLevel.NONE)

    await _expect_error(
        "summary_star_limit_exceeded",
        memoryService.update_memory(
            _TEAM_ID, _AGENT_ID, plain.id, star_level="summary",
        ),
    )


async def test_title_to_summary_upgrade_hits_summary_limit(memory_db):
    """`title -> summary` 升级超限要拦，且**自身原 title 级别不误报总数上限**。

    这是配额统计最容易错的一处：统计必须排除自身 id，否则这条记忆原本的 TITLE
    会被算进 `other_total`，让「总数刚好 12 时标题升概要」被误判为 `star_limit_exceeded`。
    """
    for index in range(MEMORY_STAR_SUMMARY_LIMIT):
        await _create(f"概要{index}", star_level=MemoryStarLevel.SUMMARY)
    target = await _create("待升级", star_level=MemoryStarLevel.TITLE)

    exc = await _expect_error(
        "summary_star_limit_exceeded",
        memoryService.update_memory(_TEAM_ID, _AGENT_ID, target.id, star_level="summary"),
    )
    # 说明不是总数上限拦的：总数此时是 7，远低于 12
    assert exc.error_code != "star_limit_exceeded"


async def test_self_level_update_does_not_trigger_total_limit(memory_db):
    """自身原级别更新不误报：总数打满 12 时，把其中一条 TITLE 重新保存为 TITLE 必须成功。"""
    created = []
    for index in range(MEMORY_STAR_TOTAL_LIMIT):
        created.append(await _create(f"星标{index}", star_level=MemoryStarLevel.TITLE))

    updated = await memoryService.update_memory(
        _TEAM_ID, _AGENT_ID, created[-1].id, star_level="title", summary="改个概要",
    )
    assert updated.star_level is MemoryStarLevel.TITLE


async def test_archive_releases_star_quota(memory_db):
    """归档自动剥离星标并释放额度：归档一条后又能新增一条星标。"""
    created = []
    for index in range(MEMORY_STAR_TOTAL_LIMIT):
        created.append(await _create(f"星标{index}", star_level=MemoryStarLevel.TITLE))
    await _expect_error(
        "star_limit_exceeded", _create("第13条", star_level=MemoryStarLevel.TITLE),
    )

    archived = await memoryService.update_memory(
        _TEAM_ID, _AGENT_ID, created[0].id, status="archived",
    )
    assert archived.star_level is MemoryStarLevel.NONE
    assert archived.status is MemoryStatus.ARCHIVED

    detail = await _create("新的星标", star_level=MemoryStarLevel.TITLE)
    assert detail.star_level is MemoryStarLevel.TITLE


async def test_archived_memory_star_is_stripped_even_if_star_level_passed(memory_db):
    """归档 + 同时传 star_level：以「归档不占额度」为准，强制 none。"""
    detail = await _create("归档测试")
    archived = await memoryService.update_memory(
        _TEAM_ID, _AGENT_ID, detail.id, status="archived", star_level="summary",
    )
    assert archived.star_level is MemoryStarLevel.NONE


# ---------------------------------------------------------------------------
# error_code ⑨：memory_not_found
# ---------------------------------------------------------------------------


async def test_memory_not_found_for_unknown_id(memory_db):
    await _expect_error(
        "memory_not_found", memoryService.read_memory(_TEAM_ID, _AGENT_ID, 9999),
    )


async def test_memory_not_found_does_not_leak_cross_agent(memory_db):
    """别的 Agent 的记忆按 ID 读：报「不存在」，不能透露它存在。"""
    other = await _create("别人的记忆", agent_id=8)

    exc = await _expect_error(
        "memory_not_found", memoryService.read_memory(_TEAM_ID, _AGENT_ID, other.id),
    )
    assert str(other.id) in exc.error_message  # 只回显被问的 ID
    assert "agent_id" not in exc.error_message.lower()
    assert "不属于" not in exc.error_message


async def test_memory_not_found_for_cross_team(memory_db):
    other = await _create("别队记忆", team_id=99)
    await _expect_error(
        "memory_not_found", memoryService.read_memory(_TEAM_ID, _AGENT_ID, other.id),
    )


async def test_update_unknown_memory_reports_not_found(memory_db):
    await _expect_error(
        "memory_not_found",
        memoryService.update_memory(_TEAM_ID, _AGENT_ID, 9999, summary="x"),
    )


async def test_read_archived_memory_allowed(memory_db):
    """归档记忆按 ID 仍可读（刚归档想反悔的场景）。"""
    detail = await _create("归档可读")
    await memoryService.update_memory(_TEAM_ID, _AGENT_ID, detail.id, status="archived")

    read = await memoryService.read_memory(_TEAM_ID, _AGENT_ID, detail.id)
    assert read.status is MemoryStatus.ARCHIVED


# ---------------------------------------------------------------------------
# error_code ⑩：invalid_memory_query（PM 裁定 F 增补）
# ---------------------------------------------------------------------------


async def test_invalid_memory_query_empty(memory_db):
    await _expect_error(
        "invalid_memory_query",
        memoryService.search_memories(_TEAM_ID, _AGENT_ID, query="   "),
    )


# ---------------------------------------------------------------------------
# 检索：limit / 排序 / snippet
# ---------------------------------------------------------------------------


async def test_search_limit_clamped(memory_db):
    """limit 钳到 [1, 20]：极端值不放大（防把整库拉进上下文）。"""
    assert memoryService.resolve_search_limit(None) == 5
    assert memoryService.resolve_search_limit(0) == 1
    assert memoryService.resolve_search_limit(-3) == 1
    assert memoryService.resolve_search_limit(1000) == MEMORY_SEARCH_LIMIT_MAX
    assert memoryService.resolve_search_limit(MEMORY_SEARCH_LIMIT_MAX) == MEMORY_SEARCH_LIMIT_MAX


async def test_search_limit_applied(memory_db):
    for index in range(8):
        await _create(f"订单超时 {index}", content="普通订单超时 30 分钟自动关闭")
    results = await memoryService.search_memories(
        _TEAM_ID, _AGENT_ID, query="订单", limit=3,
    )
    assert len(results) == 3


async def test_search_weight_ordering(memory_db):
    """权重：命中 title(+4) > tags(+3) > summary(+2) > content(+1)。"""
    only_content = await _create(
        "无关标题A", summary="无关概要A", content="这里提到关键词 缓存", tags=[],
    )
    only_summary = await _create(
        "无关标题B", summary="关键词 缓存 的结论", content="无关正文", tags=[],
    )
    only_tags = await _create(
        "无关标题C", summary="无关概要C", content="无关正文", tags=["缓存"],
    )
    in_title = await _create(
        "缓存 策略", summary="无关概要D", content="无关正文", tags=[],
    )

    results = await memoryService.search_memories(_TEAM_ID, _AGENT_ID, query="缓存")
    assert [item.id for item in results] == [
        in_title.id, only_tags.id, only_summary.id, only_content.id,
    ]


async def test_search_multi_keyword_accumulates_score(memory_db):
    """多关键词命中同一字段会累加分数。"""
    one_hit = await _create("订单 说明", summary="x", content="y")
    two_hits = await _create("订单 超时 说明", summary="x", content="y")

    results = await memoryService.search_memories(_TEAM_ID, _AGENT_ID, query="订单 超时")
    assert [item.id for item in results] == [two_hits.id, one_hit.id]


async def test_search_excludes_archived_and_other_agent(memory_db):
    """检索只查 ACTIVE 且只看自己：归档与他人记忆都不出现。"""
    mine = await _create("订单 规范")
    archived = await _create("订单 旧规范")
    await memoryService.update_memory(_TEAM_ID, _AGENT_ID, archived.id, status="archived")
    await _create("订单 别人的", agent_id=8)

    results = await memoryService.search_memories(_TEAM_ID, _AGENT_ID, query="订单")
    assert [item.id for item in results] == [mine.id]


async def test_search_no_hit_returns_empty_list(memory_db):
    await _create("订单 规范")
    assert await memoryService.search_memories(_TEAM_ID, _AGENT_ID, query="完全不存在的词") == []


async def test_search_snippet_contains_hit_and_ellipsis(memory_db):
    """片段：命中词在中间、前后被截断时补 `…`。"""
    content = "开头" * 60 + "普通订单超时 30 分钟自动关闭" + "结尾" * 60
    await _create("订单 超时规范", content=content)

    results = await memoryService.search_memories(_TEAM_ID, _AGENT_ID, query="超时")
    assert len(results) == 1
    snippet = results[0].snippet
    assert "超时" in snippet
    assert snippet.startswith("…") and snippet.endswith("…")
    # 大致窗口：前 40 + 命中 + 后 80，允许断句对齐带来的偏差
    assert len(snippet) <= 40 + 80 + 20 + 4


async def test_search_snippet_no_hit_in_content_returns_empty(memory_db):
    """只在标题命中、正文完全没命中：snippet 返回空串（不给无关正文）。"""
    await _create("缓存 策略", summary="无关", content="正文完全没有该词")

    results = await memoryService.search_memories(_TEAM_ID, _AGENT_ID, query="缓存")
    assert len(results) == 1
    assert results[0].snippet == ""


async def test_search_snippet_aligns_to_boundary(memory_db):
    """片段向断句标点对齐：截取点落在标点之后，不切断句子。"""
    content = "第一句话。第二句话包含关键词缓存。第三句话。"
    await _create("缓存", content=content, summary="无")

    snippet = memoryService.extract_snippet(content, ["缓存"])
    assert "缓存" in snippet
    # 对齐后不应以半句开头（本例 start > 0 才会补 …，这里从「第一句」开始则不加）
    assert snippet in (content, "…" + content, content + "…")


async def test_extract_snippet_single_token_no_hit(memory_db):
    assert memoryService.extract_snippet("正文", ["不存在"]) == ""
    assert memoryService.extract_snippet("", ["正文"]) == ""


# ---------------------------------------------------------------------------
# 对外 JSON 口径（枚举小写 value / DB 存 name）
# ---------------------------------------------------------------------------


def _memory_stub(**overrides) -> GtAgentMemory:
    from datetime import datetime

    data = {
        "id": 12,
        "team_id": _TEAM_ID,
        "agent_id": _AGENT_ID,
        "title": "方案输出规范",
        "summary": "先结论后细节",
        "content": "正文",
        "star_level": MemoryStarLevel.SUMMARY,
        "status": MemoryStatus.ACTIVE,
        "tags": ["输出风格"],
        "created_at": datetime(2026, 11, 6, 10, 0, 0),
        "updated_at": datetime(2026, 11, 6, 10, 24, 0),
    }
    data.update(overrides)
    memory = GtAgentMemory(**data)
    memory.id = data["id"]
    return memory


def test_memory_detail_serializes_enum_as_lowercase_value():
    """L3 对外 JSON：`star_level` / `status` 是**小写字符串**（不是 `SUMMARY`、不是 int）。"""
    from model.coreModel.memoryModel import MemoryDetail

    payload = MemoryDetail.from_memory(_memory_stub()).model_dump(mode="json")
    assert payload["star_level"] == "summary"
    assert payload["status"] == "active"
    assert isinstance(payload["star_level"], str)


def test_memory_list_item_serializes_enum_as_lowercase_value():
    from model.coreModel.memoryModel import MemoryListItem

    payload = MemoryListItem.from_memory(_memory_stub()).model_dump(mode="json")
    assert payload["star_level"] == "summary"
    assert "content" not in payload  # L1 不带正文


def test_memory_search_item_serializes_enum_as_lowercase_value():
    from model.coreModel.memoryModel import MemorySearchItem

    payload = MemorySearchItem.from_memory(_memory_stub(), "…片段…").model_dump(mode="json")
    assert payload["star_level"] == "summary"
    assert payload["snippet"] == "…片段…"


def test_memory_api_dict_overrides_enum_to_lowercase_value():
    """HTTP 接口口径：ORM 的 `to_json()` 会给枚举 name，`memory_api_dict` 必须覆写成小写。"""
    from model.coreModel.memoryModel import memory_api_dict
    from util import jsonUtil

    memory = _memory_stub()

    # 先证明默认行为确实是大写 name（否则下面那条断言就是自证）
    raw = jsonUtil.object_to_json_data(memory)
    assert raw["star_level"] == "SUMMARY"
    assert raw["status"] == "ACTIVE"

    payload = memory_api_dict(memory)
    assert payload["star_level"] == "summary"
    assert payload["status"] == "active"


async def test_memory_detail_from_real_db_row(memory_db):
    """真实 DB 行（`EnumField` 读出枚举成员）再序列化，对外口径同样是小写。"""
    detail = await _create("序列化口径", star_level="summary")
    payload = detail.model_dump(mode="json")
    assert payload["star_level"] == "summary"
    assert payload["status"] == "active"
