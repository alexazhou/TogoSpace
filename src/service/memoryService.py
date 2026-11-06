"""Agent 长期记忆服务：校验、CRUD、多关键词检索与片段提取。

**校验为什么放在这一层**：DB 的文本列统一 `TEXT`、不施加长度约束（step2 §2.1），
标题 80 / 概要 120 / 正文 1000 / 标签 5×24 / 同名防重 / 星标配额（12 总、其中 6 概要）
全部在本模块集中拦截，后续调整配额只需改 `constants.py`，不动表结构。

**对外错误码**：一律 `raise TogoException(message, error_code)`，由
`funcToolService.run_tool_call` 转成 `{"success": False, "message": ..., "error_code": ...}`。
错误文案里**不透露跨 Agent 的存在性**——查不到一律 `memory_not_found`（step2 §7.1）。

**星标配额的两个易错点**（step2 §2.3）：
1. 统计口径是「**除本条记录外**」的活跃星标，`exclude_id` 必须传自身 id；
   否则 `title -> summary` 升级、以及同级别重复保存都会把自己算进去造成误报。
2. 归档记忆天然不占额度：`status == ACTIVE` 是硬条件，且归档时强制剥离星标。
"""
from __future__ import annotations

import logging

from constants import (
    MEMORY_CONTENT_MAX_CHARS,
    MEMORY_SEARCH_LIMIT_DEFAULT,
    MEMORY_SEARCH_LIMIT_MAX,
    MEMORY_SEARCH_LIMIT_MIN,
    MEMORY_SEARCH_WEIGHT_CONTENT,
    MEMORY_SEARCH_WEIGHT_SUMMARY,
    MEMORY_SEARCH_WEIGHT_TAGS,
    MEMORY_SEARCH_WEIGHT_TITLE,
    MEMORY_SNIPPET_ELLIPSIS,
    MEMORY_SNIPPET_PREFIX_CHARS,
    MEMORY_SNIPPET_SUFFIX_CHARS,
    MEMORY_STAR_SUMMARY_LIMIT,
    MEMORY_STAR_TOTAL_LIMIT,
    MEMORY_SUMMARY_MAX_CHARS,
    MEMORY_TAG_MAX_CHARS,
    MEMORY_TAGS_MAX_COUNT,
    MEMORY_TITLE_MAX_CHARS,
    MemoryStarLevel,
    MemoryStatus,
)
from dal.db import gtAgentMemoryManager
from exception import TogoException
from model.coreModel.memoryModel import MemoryDetail, MemoryListItem, MemorySearchItem
from model.dbModel.base import atomic
from model.dbModel.gtAgentMemory import GtAgentMemory

logger = logging.getLogger(__name__)

# 片段截取时用来对齐的中英文断句标点与换行。
_SNIPPET_BOUNDARIES = "。！？；\n\r.!?;"


# ---------------------------------------------------------------------------
# 字段校验
# ---------------------------------------------------------------------------


def _require_single_line(value: str, *, field: str, max_chars: int, error_code: str) -> str:
    """单行文本校验：去首尾空白后非空、不含换行、不超过上限。返回**清洗后**的值。"""
    cleaned = (value or "").strip()
    if not cleaned:
        raise TogoException(
            f"记忆{field}不能为空，请填写单行{field}（≤ {max_chars} 字符）",
            error_code,
        )
    if "\n" in cleaned or "\r" in cleaned:
        raise TogoException(
            f"记忆{field}不能包含换行符，请压缩为单行（≤ {max_chars} 字符）",
            error_code,
        )
    if len(cleaned) > max_chars:
        raise TogoException(
            f"记忆{field}长度 {len(cleaned)} 超过上限 {max_chars} 字符，请精简",
            error_code,
        )
    return cleaned


def validate_title(title: str) -> str:
    return _require_single_line(
        title, field="标题", max_chars=MEMORY_TITLE_MAX_CHARS, error_code="invalid_memory_title",
    )


def validate_summary(summary: str) -> str:
    return _require_single_line(
        summary, field="概要", max_chars=MEMORY_SUMMARY_MAX_CHARS, error_code="invalid_memory_summary",
    )


def validate_content(content: str) -> str:
    """正文允许换行（代码块 / 分步说明），只校验非空与长度。

    正文为空按 step2 §7.1 归 `content_too_long`（文档把「为空或超长」合并到同一码，
    这里照文档原样，不自行拆成两个码）。
    """
    cleaned = (content or "").strip()
    if not cleaned or len(cleaned) > MEMORY_CONTENT_MAX_CHARS:
        raise TogoException(
            f"记忆正文不能为空且不超过 {MEMORY_CONTENT_MAX_CHARS} 字符"
            f"（当前 {len(cleaned)} 字符），请精简或拆分为多个独立主题",
            "content_too_long",
        )
    return cleaned


def validate_tags(tags: list[str] | None) -> list[str]:
    """标签校验：去空白、丢弃空串、数量 ≤ 5、单个 ≤ 24 字符。"""
    if not tags:
        return []
    cleaned: list[str] = []
    for raw in tags:
        tag = (raw or "").strip()
        if not tag:
            continue
        if len(tag) > MEMORY_TAG_MAX_CHARS:
            raise TogoException(
                f"标签「{tag[:MEMORY_TAG_MAX_CHARS]}…」长度 {len(tag)} 超过上限 "
                f"{MEMORY_TAG_MAX_CHARS} 字符，请精简",
                "invalid_memory_tags",
            )
        cleaned.append(tag)
    if len(cleaned) > MEMORY_TAGS_MAX_COUNT:
        raise TogoException(
            f"标签数量 {len(cleaned)} 超过上限 {MEMORY_TAGS_MAX_COUNT} 个，请精简",
            "invalid_memory_tags",
        )
    return cleaned


def parse_star_level(value: str | MemoryStarLevel | None, *, default: MemoryStarLevel) -> MemoryStarLevel:
    """解析星标级别参数（大小写不敏感，接受 name 或 value）。

    **不新增 error_code**：step2 §7.1 只有九条，PM 裁定 F 只批了 `invalid_memory_query`
    一条（10 条）。非法星标/状态属于参数格式问题，message 已列全部有效值足够自纠，
    故抛不带 code 的 `TogoException`（工具层返回 `{"success": False, "message": ...}`）。
    """
    if value is None or value == "":
        return default
    if isinstance(value, MemoryStarLevel):
        return value
    parsed = MemoryStarLevel.value_of(str(value))
    if parsed is None:
        valid = " / ".join(level.value for level in MemoryStarLevel)
        raise TogoException(f"无效的星标级别：{value}，有效值为 {valid}")
    return parsed


def parse_status(value: str | MemoryStatus | None, *, default: MemoryStatus) -> MemoryStatus:
    """解析记忆状态参数（口径同 `parse_star_level`，不新增 error_code）。"""
    if value is None or value == "":
        return default
    if isinstance(value, MemoryStatus):
        return value
    parsed = MemoryStatus.value_of(str(value))
    if parsed is None:
        valid = " / ".join(status.value for status in MemoryStatus)
        raise TogoException(f"无效的记忆状态：{value}，有效值为 {valid}")
    return parsed


# ---------------------------------------------------------------------------
# 配额校验
# ---------------------------------------------------------------------------


async def _assert_star_quota(
    team_id: int,
    agent_id: int,
    *,
    star_level: MemoryStarLevel,
    exclude_id: int | None,
) -> None:
    """星标配额双重校验（step2 §2.3）。`star_level == NONE` 时直接放行。"""
    if star_level == MemoryStarLevel.NONE:
        return

    other_total = await gtAgentMemoryManager.count_active_starred(
        team_id, agent_id, exclude_id=exclude_id,
    )
    if other_total + 1 > MEMORY_STAR_TOTAL_LIMIT:
        raise TogoException(
            f"活跃星标已达上限 {MEMORY_STAR_TOTAL_LIMIT} 条，请先把低频星标降级为 none，"
            f"或将失效记忆归档后再加星",
            "star_limit_exceeded",
        )

    if star_level != MemoryStarLevel.SUMMARY:
        return
    other_summary = await gtAgentMemoryManager.count_active_starred(
        team_id, agent_id, star_level=MemoryStarLevel.SUMMARY, exclude_id=exclude_id,
    )
    if other_summary + 1 > MEMORY_STAR_SUMMARY_LIMIT:
        raise TogoException(
            f"概要级星标已达上限 {MEMORY_STAR_SUMMARY_LIMIT} 条，"
            f"请降级为 title 星标或释放旧的 summary 星标",
            "summary_star_limit_exceeded",
        )


# ---------------------------------------------------------------------------
# 变更广播
# ---------------------------------------------------------------------------


def _publish_memory_changed(team_id: int, agent_id: int, memory_id: int, action: str) -> None:
    """广播「某个 Agent 的记忆变了」，供前端弹窗内的记忆 Tab 实时重拉。

    **只在事务提交之后调用**：事务内 publish 会让前端先于落库去查，
    查到旧数据后不再有第二次推送，界面就永远停在旧值上（比不推更糟）。

    延迟 import 与 `taskService` 同一处理方式，避免 service 模块间的循环引用；
    `messageBus.publish` 本身要求调用方在事件循环内，这里由调用链保证。
    """
    from constants import MessageBusTopic
    from service import messageBus

    messageBus.publish(
        MessageBusTopic.AGENT_MEMORY_CHANGED,
        team_id=team_id,
        agent_id=agent_id,
        memory_id=memory_id,
        action=action,
    )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


async def create_memory(
    team_id: int,
    agent_id: int,
    *,
    title: str,
    summary: str,
    content: str,
    star_level: str | MemoryStarLevel = MemoryStarLevel.NONE,
    tags: list[str] | None = None,
) -> MemoryDetail:
    """创建记忆槽位：字段校验 → 同名防重 → 星标配额 → 落库（同一事务）。"""
    clean_title = validate_title(title)
    clean_summary = validate_summary(summary)
    clean_content = validate_content(content)
    clean_tags = validate_tags(tags)
    level = parse_star_level(star_level, default=MemoryStarLevel.NONE)

    async with atomic():
        existing = await gtAgentMemoryManager.get_active_by_title(team_id, agent_id, clean_title)
        if existing is not None:
            raise TogoException(
                f"活跃记忆中已存在同名标题「{clean_title}」的记忆 #{existing.id}，"
                f"请改用 memory_update 更新该记忆，不要重复建槽",
                "duplicate_memory_title",
            )
        await _assert_star_quota(team_id, agent_id, star_level=level, exclude_id=None)

        memory = GtAgentMemory(
            team_id=team_id,
            agent_id=agent_id,
            title=clean_title,
            summary=clean_summary,
            content=clean_content,
            star_level=level,
            status=MemoryStatus.ACTIVE,
            tags=clean_tags,
        )
        await gtAgentMemoryManager.save_memory(memory, force_insert=True)

    logger.info(
        "记忆已创建: team_id=%d, agent_id=%d, memory_id=%s, star_level=%s",
        team_id, agent_id, memory.id, level.value,
    )
    _publish_memory_changed(team_id, agent_id, memory.id, "created")
    return MemoryDetail.from_memory(memory)


async def update_memory(
    team_id: int,
    agent_id: int,
    memory_id: int,
    *,
    title: str | None = None,
    summary: str | None = None,
    content: str | None = None,
    star_level: str | MemoryStarLevel | None = None,
    status: str | MemoryStatus | None = None,
    tags: list[str] | None = None,
) -> MemoryDetail:
    """部分更新：**未传 = 不改**（`None` 才算未传，空串会走对应字段校验）。

    关键规则：
    - 改 `content` 必须同时传入非空的 `summary`，否则 `summary_required`（防概要滞后）；
    - `status="archived"` 时强制把 `star_level` 置为 `none`（自动释放星标槽位）；
    - 目标为 `ACTIVE` 且带星标时按「除本条外」重新校验配额。
    """
    memory = await gtAgentMemoryManager.get_memory(team_id, agent_id, memory_id)
    if memory is None:
        # 统一报不存在：不区分「ID 不存在」与「属于别的 Agent」，防跨权限探测。
        raise TogoException(f"记忆 #{memory_id} 不存在", "memory_not_found")

    target_status = parse_status(status, default=memory.status)
    target_level = parse_star_level(star_level, default=memory.star_level)

    clean_title = memory.title if title is None else validate_title(title)
    clean_content = memory.content if content is None else validate_content(content)

    if content is not None:
        # 改正文必须同步给概要：`summary` 缺省或空白都算没给。
        if summary is None or not str(summary).strip():
            raise TogoException(
                "修改正文时必须同时传入最新的 summary，保证概要（L2）与正文（L3）一致",
                "summary_required",
            )
    clean_summary = memory.summary if summary is None else validate_summary(summary)

    clean_tags = list(memory.tags or []) if tags is None else validate_tags(tags)

    # 归档强制剥离星标：无论是本次显式传 status，还是显式传了 status + star_level，
    # 都以「归档后不占配额」为准（step2 §2.3 第 1 条）。
    if target_status == MemoryStatus.ARCHIVED:
        target_level = MemoryStarLevel.NONE

    async with atomic():
        if clean_title != memory.title:
            existing = await gtAgentMemoryManager.get_active_by_title(
                team_id, agent_id, clean_title, exclude_id=memory.id,
            )
            if existing is not None:
                raise TogoException(
                    f"活跃记忆中已存在同名标题「{clean_title}」的记忆 #{existing.id}，"
                    f"请改用 memory_update 更新该记忆",
                    "duplicate_memory_title",
                )

        if target_status == MemoryStatus.ACTIVE:
            await _assert_star_quota(
                team_id, agent_id, star_level=target_level, exclude_id=memory.id,
            )

        memory.title = clean_title
        memory.summary = clean_summary
        memory.content = clean_content
        memory.star_level = target_level
        memory.status = target_status
        memory.tags = clean_tags
        await gtAgentMemoryManager.save_memory(
            memory,
            only=[
                GtAgentMemory.title,
                GtAgentMemory.summary,
                GtAgentMemory.content,
                GtAgentMemory.star_level,
                GtAgentMemory.status,
                GtAgentMemory.tags,
            ],
        )

    logger.info(
        "记忆已更新: team_id=%d, agent_id=%d, memory_id=%d, star_level=%s, status=%s",
        team_id, agent_id, memory_id, target_level.value, target_status.value,
    )
    _publish_memory_changed(team_id, agent_id, memory_id, "updated")
    return MemoryDetail.from_memory(memory)


async def read_memory(team_id: int, agent_id: int, memory_id: int) -> MemoryDetail:
    """按 ID 读取完整详情（含已归档）。归属校验失败统一 `memory_not_found`。"""
    memory = await gtAgentMemoryManager.get_memory(team_id, agent_id, memory_id)
    if memory is None:
        raise TogoException(f"记忆 #{memory_id} 不存在", "memory_not_found")
    return MemoryDetail.from_memory(memory)


async def list_memories(
    team_id: int,
    agent_id: int,
    *,
    include_archived: bool = False,
) -> list[MemoryListItem]:
    """L1 目录：默认只返回 `ACTIVE`；`include_archived=True` 时一并返回归档。

    Agent 侧要拿到归档记忆的 ID 只能靠这个开关——`memory_search` 只查活跃，
    否则「归档后想反悔恢复」就没有入口（step2 §4.2 允许 `memory_read` 读归档，
    但没定义 ID 从哪来）。
    """
    memories = await gtAgentMemoryManager.list_memories(
        team_id, agent_id, include_archived=include_archived,
    )
    return [MemoryListItem.from_memory(memory) for memory in memories]


# ---------------------------------------------------------------------------
# 检索
# ---------------------------------------------------------------------------


def resolve_search_limit(limit: int | None) -> int:
    """把 `memory_search` 的 limit 钳到 `[1, 20]`（默认 5）。

    不钳的话 Agent 传 1000 就等于把整库内容一次性拉进上下文，与
    「L2 只给候选」的设计目的直接冲突。
    """
    if limit is None:
        return MEMORY_SEARCH_LIMIT_DEFAULT
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return MEMORY_SEARCH_LIMIT_DEFAULT
    return max(MEMORY_SEARCH_LIMIT_MIN, min(value, MEMORY_SEARCH_LIMIT_MAX))


def tokenize_query(query: str) -> list[str]:
    """按空白切分查询词；空 query 直接报 `invalid_memory_query`。

    空 query 若放行，打分函数会把「所有记忆都得 0 分」的结果原样返回，
    对 Agent 是纯噪声。
    """
    tokens = [token.strip() for token in (query or "").split() if token.strip()]
    if not tokens:
        raise TogoException(
            "检索关键词不能为空，请提供至少一个关键词（多个关键词用空格分隔）",
            "invalid_memory_query",
        )
    return tokens


def score_memory(memory: GtAgentMemory, tokens: list[str]) -> int:
    """按字段权重打分：title +4、tags +3、summary +2、content +1（每个关键词每字段计一次）。"""
    title = memory.title or ""
    summary = memory.summary or ""
    content = memory.content or ""
    tags_text = " ".join(memory.tags or [])

    score = 0
    for token in tokens:
        lowered = token.lower()
        if lowered in title.lower():
            score += MEMORY_SEARCH_WEIGHT_TITLE
        if lowered in tags_text.lower():
            score += MEMORY_SEARCH_WEIGHT_TAGS
        if lowered in summary.lower():
            score += MEMORY_SEARCH_WEIGHT_SUMMARY
        if lowered in content.lower():
            score += MEMORY_SEARCH_WEIGHT_CONTENT
    return score


def extract_snippet(content: str, tokens: list[str]) -> str:
    """截取首个命中关键词周围的关键片段（前约 40、后约 80 字符）。

    对齐规则：向最近的中英文断句标点或换行符对齐，被截断的一侧补 `…`。
    无任何命中时返回空串（不给 Agent 一段与查询无关的正文）。
    """
    if not content:
        return ""

    lowered = content.lower()
    hit_pos = -1
    hit_token = ""
    for token in tokens:
        pos = lowered.find(token.lower())
        if pos == -1:
            continue
        # 取「最靠前」的命中，让片段从正文早段开始，读起来更连贯。
        if hit_pos == -1 or pos < hit_pos:
            hit_pos = pos
            hit_token = token
    if hit_pos == -1:
        return ""

    start = max(0, hit_pos - MEMORY_SNIPPET_PREFIX_CHARS)
    end = min(len(content), hit_pos + len(hit_token) + MEMORY_SNIPPET_SUFFIX_CHARS)

    if start > 0:
        # 向后对齐到最近断句标点之后（不越过命中位置）
        boundary = _find_forward_boundary(content, start, hit_pos)
        if boundary is not None:
            start = boundary
    if end < len(content):
        # 向前对齐到最近断句标点（不早于命中结束位置）
        boundary = _find_backward_boundary(content, hit_pos + len(hit_token), end)
        if boundary is not None:
            end = boundary

    snippet = content[start:end].strip()
    if not snippet:
        return ""
    if start > 0:
        snippet = MEMORY_SNIPPET_ELLIPSIS + snippet
    if end < len(content):
        snippet = snippet + MEMORY_SNIPPET_ELLIPSIS
    return snippet


def _find_forward_boundary(content: str, start: int, limit: int) -> int | None:
    """从 `start` 向后找第一个断句标点，返回其后一位；找不到返回 None。"""
    for index in range(start, max(start, limit)):
        if content[index] in _SNIPPET_BOUNDARIES:
            return index + 1
    return None


def _find_backward_boundary(content: str, floor: int, end: int) -> int | None:
    """从 `end-1` 向前找第一个断句标点，返回其后一位；找不到返回 None。"""
    for index in range(end - 1, max(floor, 0) - 1, -1):
        if content[index] in _SNIPPET_BOUNDARIES:
            return index + 1
    return None


async def search_memories(
    team_id: int,
    agent_id: int,
    *,
    query: str,
    limit: int | None = None,
) -> list[MemorySearchItem]:
    """多关键词加权检索（只查 `ACTIVE`），返回 L2 候选列表。"""
    tokens = tokenize_query(query)
    resolved_limit = resolve_search_limit(limit)

    candidates = await gtAgentMemoryManager.list_active_for_search(team_id, agent_id)
    scored: list[tuple[int, GtAgentMemory]] = []
    for memory in candidates:
        score = score_memory(memory, tokens)
        if score > 0:
            scored.append((score, memory))

    # 总分倒序，同分按更新时间倒序（更新时间也相同再按 id 倒序，保证结果稳定可断言）。
    scored.sort(key=lambda item: (item[0], item[1].updated_at, item[1].id), reverse=True)

    return [
        MemorySearchItem.from_memory(memory, extract_snippet(memory.content, tokens))
        for _, memory in scored[:resolved_limit]
    ]


# ---------------------------------------------------------------------------
# 供 promptBuilder / controller 使用
# ---------------------------------------------------------------------------


async def get_starred_memories(team_id: int, agent_id: int) -> list[GtAgentMemory]:
    """活跃星标记忆（System Prompt 快照装配用），按 SUMMARY 优先、`updated_at` 倒序。"""
    if team_id <= 0 or agent_id <= 0:
        return []
    return await gtAgentMemoryManager.list_starred_memories(team_id, agent_id)


async def get_agent_memories_for_view(
    team_id: int,
    agent_id: int,
    *,
    include_archived: bool = False,
) -> list[GtAgentMemory]:
    """前端「记忆」Tab 的只读列表（星标优先，同级按 `updated_at` 倒序）。"""
    return await gtAgentMemoryManager.list_memories(
        team_id, agent_id, include_archived=include_archived,
    )
