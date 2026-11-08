"""容器说明字段 `note` 的服务层用例（#153 判据 C1~C4）。

用 `test_container_core.env` 那套夹具（假 docker 可执行文件 + 跑完整迁移的临时库），
这样「DB 里有没有值」「docker 到底被调了几次」都能直接断言，不需要 mock 掉被测逻辑。

口径（Operator 2026-11-08 已确认）：
- 字段名 `note`，≤200 字符，可空；
- 超长**报错**、不截断；
- 只创建时写，不提供修改工具（本文件不测修改）；
- 未填写一律存 `NULL`（不存空串），`list_containers` 里呈现为 `null`。
"""

from __future__ import annotations

import pytest

from exception import TogoException
from service import containerService
from service.containerService import core

# 复用 create 测试里的环境夹具（假 docker + 真实迁移链 + 临时库）
from .test_container_core import env as container_env  # noqa: F401


@pytest.fixture
def fake(container_env):  # noqa: F811
    return container_env


# ---------------------------------------------------------------------------
# C1：传 note → DB 有值，且 list_containers 的容器项里能看见
# ---------------------------------------------------------------------------


async def test_create_container_persists_note_and_list_exposes_it(fake):
    """`note="跑前端构建"` ⇒ DB 该行为该值，且列表项的 `to_tool_dict()` 里真有 `note`。

    断言的是**列表返回体里的键**，不是「DB 有这一列」：模型只看得见 `to_tool_dict()`
    产出的字典，键漏了就等于没这个字段。
    """
    record = await core.create_container(1, note="跑前端构建")

    assert record.note == "跑前端构建"
    # 重新从 DB 读一遍，证明是落库了而不是只在内存对象上
    reloaded = await core.gtAgentContainerManager.get_active_by_id(record.id)
    assert reloaded is not None
    assert reloaded.note == "跑前端构建"

    infos = await containerService.list_agent_containers(1, docker_available=False)
    assert len(infos) == 1
    as_tool = infos[0].to_tool_dict()
    assert "note" in as_tool, "note 不在工具白名单里，模型永远看不到自己写的说明"
    assert as_tool["note"] == "跑前端构建"


async def test_note_is_stripped_of_surrounding_whitespace(fake):
    """首尾空白去掉后再存：`"  hi  "` 落库为 `"hi"`。

    不 strip 的话「看起来一样的两条说明」在等值比较和展示上会不一致，
    而这类差异用户根本看不出来。
    """
    record = await core.create_container(1, note="  跑前端构建  ")

    assert record.note == "跑前端构建"


# ---------------------------------------------------------------------------
# C2：不传 note → 存 NULL，且全链一致呈现为 null
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("blank", [None, "", "   ", "\n\t "])
async def test_blank_note_is_stored_as_null_everywhere(fake, blank):
    """`None` / 空串 / 纯空白一律存 `NULL`（不是空串），列表里也是 `null`。

    这是 C2 要求「二选一定下来并全链一致」的那一支：选 `NULL` 的理由是存量行本来
    就是 NULL，用空串表达「没写」会让升级后的老数据和新数据看起来不一样。
    """
    record = await core.create_container(1, note=blank)

    assert record.note is None
    reloaded = await core.gtAgentContainerManager.get_active_by_id(record.id)
    assert reloaded is not None
    assert reloaded.note is None

    infos = await containerService.list_agent_containers(1, docker_available=False)
    as_tool = infos[0].to_tool_dict()
    assert as_tool["note"] is None


async def test_create_container_without_note_argument_still_works(fake):
    """完全不传 `note`（旧调用方式）必须照旧成功——加字段不能变成破坏性变更。"""
    record = await core.create_container(1)

    assert record.note is None
    assert record.container_id == "c0ffee123456"


async def test_legacy_rows_without_note_are_listed_without_error(fake):
    """C5 的列表侧：升级前就存在的老行（`note` 为 NULL）能被 `list_containers` 正常列出。

    直接走 DAL 插入 = 模拟「0021 之前写入的存量行」，不经过 `create_container`
    （那条路径现在总会写 note 字段）。老行读出来是 `None` 而不是 `AttributeError`
    或空串，才说明「升级后列表不炸」。
    """
    legacy = await core.gtAgentContainerManager.create(
        agent_id=1, container_name="agent-t1-a-1", image="ubuntu:24.04"
    )
    await core.gtAgentContainerManager.set_container_id(legacy, "abcdef123456")

    infos = await containerService.list_agent_containers(1, docker_available=False)

    assert len(infos) == 1
    assert infos[0].to_tool_dict()["note"] is None
    assert infos[0].model_dump(mode="json")["record"]["note"] is None


# ---------------------------------------------------------------------------
# C3：超长报错，且**没有任何副作用**
# ---------------------------------------------------------------------------


async def test_note_longer_than_limit_fails_with_dedicated_error_code(fake):
    """201 字符 ⇒ `container_note_too_long`，不是静默截断。

    用独立错误码而不复 `container_invalid_params`：后者现在的语义是「ID 传参二选一」，
    混在一起模型不知道该改哪个参数。
    """
    before_calls = len(fake.calls())

    with pytest.raises(TogoException) as excinfo:
        await core.create_container(1, note="a" * 201)

    assert excinfo.value.error_code == core.ERROR_CONTAINER_NOTE_TOO_LONG
    # 副作用断言：一条 DB 记录都不许有、一次 docker 调用都不许发
    assert await core.gtAgentContainerManager.list_active_by_agent(1) == []
    assert len(fake.calls()) == before_calls == 0


async def test_note_validation_happens_before_quota_and_image_checks(fake):
    """note 超长的报错必须**先于**镜像探测：参数错误不该等 docker 往返。

    用「镜像设成 windows（会在镜像校验处报另一种错）」来证顺序：若 note 校验排在
    镜像校验之后，这里会拿到 `container_image_not_linux`，说明参数错误被副作用挡在后面。
    """
    fake.set_image_os("windows")

    with pytest.raises(TogoException) as excinfo:
        await core.create_container(1, note="a" * 201)

    assert excinfo.value.error_code == core.ERROR_CONTAINER_NOTE_TOO_LONG
    # 镜像探测一次都没发
    assert fake.calls_of("image") == []


# ---------------------------------------------------------------------------
# C4：边界 200 字符成功
# ---------------------------------------------------------------------------


async def test_note_at_exact_limit_is_accepted(fake):
    """恰好 200 字符必须成功（上限是闭区间），且原样落库不裁剪。"""
    note = "a" * core.MAX_CONTAINER_NOTE_LENGTH

    record = await core.create_container(1, note=note)

    assert record.note == note
    assert len(record.note) == 200


async def test_note_length_counts_characters_not_bytes(fake):
    """上限按**字符**算：200 个中文（600 字节）必须通过。

    按字节算的话中文用户实际只能写 66 个字，而错误提示写的是「200 字符」，
    用户会以为系统坏了。
    """
    note = "说" * core.MAX_CONTAINER_NOTE_LENGTH

    record = await core.create_container(1, note=note)

    assert record.note == note
    assert len(note.encode("utf-8")) == 600


async def test_non_string_note_is_rejected(fake):
    """非字符串直接拒绝（service 是公共入口，不只被工具层调用）。"""
    with pytest.raises(TogoException) as excinfo:
        await core.create_container(1, note=123)  # type: ignore[arg-type]

    assert excinfo.value.error_code == core.ERROR_CONTAINER_INVALID_PARAMS
    assert await core.gtAgentContainerManager.list_active_by_agent(1) == []


# ---------------------------------------------------------------------------
# 归一化函数本身（纯函数，语义集中在一处）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        ("", None),
        ("   ", None),
        (" hi ", "hi"),
        ("a" * 200, "a" * 200),
    ],
)
def test_normalize_container_note_table(value, expected):
    """归一化规则逐项钉住：空白 → None，其余去掉首尾空白原样保留。"""
    assert core.normalize_container_note(value) == expected
