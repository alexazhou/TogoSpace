"""`containerService.update_container_note` 的服务层用例（#203）。

与本文件上半部分（`create_container` 的 note 契约）共用同一套夹具：假 docker +
跑完整迁移的临时库。note 的**契约边界**（200 上限 / 空串清空 / 超长不截断）已经在
创建路径上钉过一次，这里刻意**再钉一遍修改路径**：两条路径是两份代码，共用
`normalize_container_note` 只是当下的实现方式，不是可依赖的事实 —— 一旦有人给修改
路径单独写一套校验，上面那批用例全绿而这里会红。

`update_container_note` 不调 docker（note 是本系统自己的元信息），所以本文件的所有
用例都可以额外断言「一次 docker 调用都没发」。
"""

from __future__ import annotations

import pytest

from exception import TogoException
from service import containerService
from service.containerService import core

from .test_container_core import env as container_env  # noqa: F401


@pytest.fixture
def fake(container_env):  # noqa: F811
    return container_env


async def _create(agent_id: int = 1, note: str | None = "原始说明"):
    """建一个容器记录并返回（走真实 create 路径，docker 是假的）。"""
    return await core.create_container(agent_id, note=note)


# ---------------------------------------------------------------------------
# D1：改得动，且 list_containers 读得到新值
# ---------------------------------------------------------------------------


async def test_update_note_by_container_id_persists_and_is_listed(fake):
    """按 12 位 ID 改说明 ⇒ 落库 + `to_tool_dict()` 里是新值。

    断言的是**列表返回体的键**（模型唯一看得见的东西），不是「内存对象上改了」：
    只改内存不落库时 `list_containers` 依然吐旧值，而这里会红。
    """
    record = await _create()
    assert record.note == "原始说明"

    updated = await core.update_container_note(
        1, container_id=record.container_id, note="跑前端构建"
    )

    assert updated.note == "跑前端构建"
    reloaded = await core.gtAgentContainerManager.get_active_by_id(record.id)
    assert reloaded is not None
    assert reloaded.note == "跑前端构建"

    infos = await containerService.list_agent_containers(1, docker_available=False)
    assert [info.to_tool_dict()["note"] for info in infos] == ["跑前端构建"]
    # note 是本系统元信息，改它不该碰 docker
    assert fake.calls_of("exec") == []
    assert fake.calls_of("rm") == []


async def test_update_note_by_record_id_works_for_pre_record(fake):
    """预记录（`container_id` 还是 NULL）只能用记录编号定位，也必须能改。"""
    pre = await core.gtAgentContainerManager.create(
        agent_id=1, container_name="agent-t1-a-99", image="ubuntu:24.04", note="旧"
    )
    assert pre.container_id is None

    updated = await core.update_container_note(1, record_id=pre.id, note="新")

    assert updated.note == "新"
    infos = await containerService.list_agent_containers(1, docker_available=False)
    assert [info.to_tool_dict()["note"] for info in infos] == ["新"]


async def test_update_note_strips_surrounding_whitespace(fake):
    """首尾空白去掉再存，与创建路径同一口径。"""
    record = await _create()

    updated = await core.update_container_note(
        1, container_id=record.container_id, note="  跑前端构建  "
    )

    assert updated.note == "跑前端构建"


# ---------------------------------------------------------------------------
# D2：清空
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("blank", ["", "   ", "\n\t "])
async def test_empty_note_clears_it(fake, blank):
    """空串 / 纯空白 ⇒ 说明被清空（存 NULL，不是空串）。

    「清空」是本单唯一的清除手段，必须真的把旧值抹掉：老值还在的话，
    人在控制台上「清空说明」之后 `list_containers` 依然显示旧说明。
    """
    record = await _create(note="要清掉的旧说明")

    updated = await core.update_container_note(
        1, container_id=record.container_id, note=blank
    )

    assert updated.note is None
    reloaded = await core.gtAgentContainerManager.get_active_by_id(record.id)
    assert reloaded is not None and reloaded.note is None
    infos = await containerService.list_agent_containers(1, docker_available=False)
    assert infos[0].to_tool_dict()["note"] is None


# ---------------------------------------------------------------------------
# D3：200 字符上限（闭区间 + 超长报错 + 无副作用）
# ---------------------------------------------------------------------------


async def test_note_at_exact_limit_is_accepted(fake):
    """恰好 200 字符必须成功，且原样落库不裁剪。"""
    record = await _create()
    note = "a" * core.MAX_CONTAINER_NOTE_LENGTH

    updated = await core.update_container_note(
        1, container_id=record.container_id, note=note
    )

    assert updated.note == note
    assert len(updated.note) == 200


async def test_note_over_limit_errors_and_leaves_old_value_intact(fake):
    """201 字符 ⇒ `container_note_too_long`，且**旧值保持不变**（不是先清空再报错）。

    这条是「超长不截断」的反面判据：如果实现先写后校验、或者截断成 200，
    库里的值就不再是原来的「原始说明」。
    """
    record = await _create()
    before_calls = len(fake.calls())

    with pytest.raises(TogoException) as excinfo:
        await core.update_container_note(
            1, container_id=record.container_id, note="a" * 201
        )

    assert excinfo.value.error_code == core.ERROR_CONTAINER_NOTE_TOO_LONG
    reloaded = await core.gtAgentContainerManager.get_active_by_id(record.id)
    assert reloaded is not None and reloaded.note == "原始说明"
    assert len(fake.calls()) == before_calls


# ---------------------------------------------------------------------------
# D4：归属与不存在（不泄露存在性）
# ---------------------------------------------------------------------------


async def test_cannot_update_another_agents_container(fake):
    """别人的容器一律当作不存在（同一个错误码/文案），不能静默成功。

    两种寻址方式都要测：一条走 `container_id`（DAL 自带 agent 维度过滤），
    一条走 `record_id`（先按主键取、再校验归属）—— 两份代码，漏一处就是越权。

    别人的记录直接用 DAL 插（不走 `create_container`：那条路要先有另一个 Agent 的
    真实上下文，会把本用例的判据从「归属校验」扯到「夹具造 Agent 上」）。
    """
    others = await core.gtAgentContainerManager.create(
        agent_id=2, container_name="agent-t1-others-1", image="ubuntu:24.04", note="别人的说明"
    )
    await core.gtAgentContainerManager.set_container_id(others, "beefbeef0001")

    with pytest.raises(TogoException) as by_id:
        await core.update_container_note(
            1, container_id=others.container_id, note="我要改"
        )
    assert by_id.value.error_code == core.ERROR_CONTAINER_NOT_FOUND

    with pytest.raises(TogoException) as by_record:
        await core.update_container_note(1, record_id=others.id, note="我要改")
    assert by_record.value.error_code == core.ERROR_CONTAINER_NOT_FOUND
    # 文案也必须一致：给出「存在但不属于你」的差异文案，就成了容器存在性探针
    assert str(by_id.value) == str(by_record.value)

    reloaded = await core.gtAgentContainerManager.get_active_by_id(others.id)
    assert reloaded is not None and reloaded.note == "别人的说明"


async def test_unknown_container_reports_not_found(fake):
    """不存在的容器：`container_not_found`。"""
    with pytest.raises(TogoException) as excinfo:
        await core.update_container_note(1, container_id="deadbeef0000", note="x")
    assert excinfo.value.error_code == core.ERROR_CONTAINER_NOT_FOUND

    with pytest.raises(TogoException) as by_record:
        await core.update_container_note(1, record_id=9999, note="x")
    assert by_record.value.error_code == core.ERROR_CONTAINER_NOT_FOUND


async def test_soft_deleted_container_is_not_addressable(fake):
    """已软删除的记录不能再改（活跃记录才可寻址）。"""
    record = await _create()
    await core.gtAgentContainerManager.mark_deleted(record)

    for kwargs in ({"container_id": record.container_id}, {"record_id": record.id}):
        with pytest.raises(TogoException) as excinfo:
            await core.update_container_note(1, note="x", **kwargs)
        assert excinfo.value.error_code == core.ERROR_CONTAINER_NOT_FOUND


# ---------------------------------------------------------------------------
# D5：参数形态（与 delete_container 同一套）
# ---------------------------------------------------------------------------


async def test_both_identifiers_given_is_rejected(fake):
    """`container_id` 与 `record_id` 都传 ⇒ `container_invalid_params`（要求二选一）。"""
    record = await _create()

    with pytest.raises(TogoException) as excinfo:
        await core.update_container_note(
            1, container_id=record.container_id, record_id=record.id, note="x"
        )

    assert excinfo.value.error_code == core.ERROR_CONTAINER_INVALID_PARAMS


async def test_neither_identifier_given_is_rejected(fake):
    """都不传也是 `container_invalid_params`，且不许顺手改到任意一条记录上。"""
    await _create()

    with pytest.raises(TogoException) as excinfo:
        await core.update_container_note(1, note="x")

    assert excinfo.value.error_code == core.ERROR_CONTAINER_INVALID_PARAMS
    infos = await containerService.list_agent_containers(1, docker_available=False)
    assert [info.to_tool_dict()["note"] for info in infos] == ["原始说明"]


@pytest.mark.parametrize("bad", ["abc", "c0ffee12345", "c0ffee1234567", "ZZZZZZZZZZZZ"])
async def test_non_short_id_container_id_is_rejected(fake, bad):
    """`container_id` 不是 12 位十六进制 ⇒ `container_invalid_params`。"""
    with pytest.raises(TogoException) as excinfo:
        await core.update_container_note(1, container_id=bad, note="x")
    assert excinfo.value.error_code == core.ERROR_CONTAINER_INVALID_PARAMS


@pytest.mark.parametrize("bad", [0, -1])
async def test_non_positive_record_id_is_rejected(fake, bad):
    """`record_id` 非正 ⇒ `container_invalid_params`。"""
    with pytest.raises(TogoException) as excinfo:
        await core.update_container_note(1, record_id=bad, note="x")
    assert excinfo.value.error_code == core.ERROR_CONTAINER_INVALID_PARAMS
