"""#145 §7.2 七条功能回归：Agent 缓存一致性（开发自测）。

对应 `shared/need-fix-2-缓存一致性-20261107/need_fix_2.md` §7.2 的七条要求。
每条用例都自带「能被变异捕获」的证据（见 docstring 与交付说明的变异矩阵），
不是恒真断言。

⚠️ **本文件不是验收判据。** 验收判据归验收人（李哥），在
`shared/need-fix-2-缓存一致性-20261107/判据探针-test_lg145_contract.py`（14 条）。
本文件是开发自测，两者**并存**，谁也不取代谁。对照关系：

| 本文件 | 李哥判据 | 关系 |
|---|---|---|
| R1 | B1 | 同一条（§7.2①），独立实现 |
| R2 | B2 | 同一条（§7.2②），独立实现 |
| R3a | B3 | **不同构造**：B3 走写路径（batch_save 的 finally invalidate，天然清）；R3a 走读路径（事务内读未命中→回填未提交值），B3 覆盖不到 |
| R3b | B9 | R3b 加强：B9 改 `_new_agent()` 返回的实例，脏值没落到缓存对象上（恒真）；R3b 先取缓存实例再改 |
| R4 | B5 / B13 | **场景覆盖有用、判别力不足**，两个属性都在：①覆盖「换库 + ID 复用」这个场景本身（B5 验「换库不清缓存」、B13 验「在途查询不回填」，**没有一条验 ID 复用**）；②判别力弱——`unbind_database()` 自身会清，所以它验的是「挂点存在」，不是在途读取跨换库。在途那条以 B13 为准 |
| R5 | B7 | 同一条（§7.2⑤），独立实现 |
| R6 | B6 / B6b | 采用 B6b 的 monkeypatch 方式（B6 的 unbind 方式只证明「没查库」，B6b 还证明「没调查询入口」） |
| R7 | — | **本文件独有**：`save_agent` 的写后热更新语义（§7.2⑦） |
| R8 | B12 | 同一条（洞1：批量读回填检查），独立实现 |
| R9 | S4 | **同一行为、两个层次，不替代**：R9 是**契约层**——断言「单 id 失效不连带阻断其他 id 的在途回填」这个**行为承诺**；S4 是**收益层**——断言「少做无谓失效」这个效率收益（红了=命中率退化，不是功能坏）。李哥把 M3 定成 S4 而非 B14，理由是「不回填在功能上仍正确，写成契约就是过度约束」，那是对**他判据包**的分层，不是对 R9 的否定 |

**李哥独有、本文件没有的**：B4（§4.2 已知残留，断言缺陷存在）、B8（写入口不得缓存调用方对象）、
B10 / B11（controller 真实写入口的成功与失败路径）。这几条都从**用户真实入口**出发，
开发自测没有覆盖上游入口，是缺口。

**李哥的 S0~S4 成本/收益判据本文件完全没有**——那组测的是「SQL 次数 / 缓存命中率」，
不是功能契约，开发自测不重复。

⚠️ **R9 与 S4 的关系**：同一行为、两个层次，**不替代**。R9 是契约层（「精确失效」这个
行为承诺），S4 是收益层（「少做无谓失效」这个效率收益，红了=命中率退化而不是功能坏）。
李哥把 M3 落成 S4 而非 B14，理由是「不回填在功能上仍然正确，写成契约就是过度约束」——
那是对**他判据包**的分层，不是对 R9 的否定。**R9 不能因为 S4 存在而删掉。**
"""
from __future__ import annotations

import asyncio
import sqlite3

import pytest

import db as db_tool
import service.ormService as ormService
from constants import DriverType, EmployStatus
from dal.db import gtAgentManager
from model.dbModel.base import atomic, bind_database, unbind_database
from model.dbModel.gtAgent import GtAgent
from service.ormService import AioSqliteDatabase

_TEAM_ID = 2


@pytest.fixture
async def agent_db(tmp_path):
    """真实迁移建库 + 绑定；退出必解绑并清缓存（避免跨用例污染）。"""
    db_path = tmp_path / "cache_consistency.db"
    db_tool.migrate_database(str(db_path))
    db = AioSqliteDatabase(str(db_path), pool_params={"timeout": 30})
    bind_database(db)
    await db.aio_connect()
    gtAgentManager.clear_agent_cache()
    try:
        yield db
    finally:
        gtAgentManager.clear_agent_cache()
        await db.aio_close()
        unbind_database()


async def _new_agent(name: str, status: EmployStatus = EmployStatus.ON_BOARD) -> GtAgent:
    """建一个 agent 并**重新从库里读回**（insert_many 不回填 id）。"""
    agent = GtAgent(
        team_id=_TEAM_ID,
        name=name,
        role_template_id=0,
        employ_status=status,
        driver=DriverType.NATIVE,
        model="m0",
        i18n={},
        allow_tools=["t1"],
        allow_skills=[],
    )
    await gtAgentManager.batch_save_agents(_TEAM_ID, [agent])
    created = await GtAgent.aio_get_or_none(GtAgent.name == name)
    assert created is not None, f"agent {name!r} 未落库"
    return created


async def _db_status(agent_id: int, db) -> str | None:
    """绕过 DAL 直接读库，作为「事实」基准。"""

    def _read():
        conn = sqlite3.connect(str(db.database))
        try:
            row = conn.execute(
                "SELECT employ_status FROM agents WHERE id = ?", (agent_id,)
            ).fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    return await asyncio.to_thread(_read)


# ── §7.2① 按 ID 读 → 写 → 再读与库一致 ───────────────────────────────────────


async def test_r1_status_update_then_id_read_matches_db(agent_db):
    """§7.2①：按 ID 读 → 批量更新状态 → 再读，结果须与库一致。

    变异：拆掉 `batch_update_agent_status` 末尾的 invalidate ⇒ 缓存仍是 ON_BOARD。
    """
    agent = await _new_agent("r1")
    await gtAgentManager.get_agent_by_id(agent.id)  # 预热缓存

    await gtAgentManager.batch_update_agent_status([agent.id], EmployStatus.OFF_BOARD)

    got = await gtAgentManager.get_agent_by_id(agent.id)
    db_value = await _db_status(agent.id, agent_db)
    assert db_value == "OFF_BOARD", "库应已更新"
    assert got is not None
    assert got.employ_status == EmployStatus.OFF_BOARD, (
        f"按 ID 读命中旧缓存：cache={got.employ_status.name} db={db_value}"
    )


# ── §7.2② 团队查询对象写入后 ID 查询不返旧值 ─────────────────────────────────


async def test_r2_team_query_write_then_id_read_not_stale(agent_db):
    """§7.2②：用 `get_team_all_agents` 拿到另一个对象写入后，ID 查询不返旧缓存。

    变异：拆掉 `batch_save_agents` 的 invalidate ⇒ ID 读仍拿旧 model。
    """
    agent = await _new_agent("r2")
    await gtAgentManager.get_agent_by_id(agent.id)  # 缓存住旧值

    team_rows = await gtAgentManager.get_team_all_agents(_TEAM_ID)
    target = next(row for row in team_rows if row.id == agent.id)
    target.model = "changed_via_team_query"
    await gtAgentManager.batch_save_agents(_TEAM_ID, [target])

    got = await gtAgentManager.get_agent_by_id(agent.id)
    assert got is not None
    assert got.model == "changed_via_team_query", (
        f"团队查询写入后 ID 读仍是旧值：cache={got.model!r}"
    )


# ── §7.2③ 回滚 / 写失败 / 共享对象修改不留未提交值 ───────────────────────────


@pytest.mark.xfail(
    strict=True,
    reason=(
        "R3a：事务内读回填未提交值的场景，当前无调用路径可达"
        "（6 个 atomic 块：agentService/core.py:704、containerService/core.py:446、"
        "taskService.py:170/301、memoryService.py:255/331 内均无 agent 缓存填充路径"
        "——注意不是「无 agent 读」：core.py:704 块内 batch_save_agents 会跑 "
        "get_max_employee_number() → SELECT MAX(employee_number)，读 agents 表但"
        "不过缓存、不回填；src/ 下 agent 的 aio_save 仅 gtAgentManager 两处）。"
        "若将来新增此类路径，本用例会转 XPASS(strict) 并报红，届时再补实现。"
    ),
)
async def test_r3a_rollback_must_not_leave_cache_value(agent_db):
    """§7.2③：外层事务回滚后，缓存不得留下未提交值。

    构造路径：事务内「读未命中 → 查库读到未提交值 → 回填缓存」→ 回滚。
    写入口的 `try/finally: invalidate` 挡不住这条——它不是写路径，是读路径。

    为什么现在 xfail 而不是修：**先有入口，才谈修**。上面 reason 里的
    「6 个 atomic 块内无 agent 缓存填充路径」是可验证的前提，不是「已知缺陷」的结论。
    （措辞是「无缓存填充路径」而非「无 agent 读」：`agentService/core.py:704` 块内
    `batch_save_agents` 会跑 `get_max_employee_number()` → `SELECT MAX(employee_number)`，
    读的是 agents 表，只是不过缓存、不回填 —— 前提必须写得能被验，写宽了会被一句话推翻。）
    前提一变（有人新增事务内 **读 agent 并回填缓存** 的路径），strict 会让本用例报红。
    """
    agent = await _new_agent("r3a")
    # 关键：不预热缓存，让事务内读未命中 → 才会走 查库 + 填缓存
    with pytest.raises(RuntimeError):
        async with atomic():
            row = await GtAgent.aio_get_or_none(GtAgent.id == agent.id)
            assert row is not None
            row.employ_status = EmployStatus.OFF_BOARD
            await row.aio_save()
            live = await gtAgentManager.get_agent_by_id(agent.id)
            assert live is not None and live.employ_status == EmployStatus.OFF_BOARD, (
                "事务内应能读到自己的未提交写（否则本用例的构造前提不成立）"
            )
            raise RuntimeError("rollback")

    db_value = await _db_status(agent.id, agent_db)
    assert db_value == "ON_BOARD", "事务应已回滚"
    cached = gtAgentManager._agent_cache.get(agent.id)
    assert cached is None or cached.employ_status == EmployStatus.ON_BOARD, (
        f"回滚后缓存留下未提交值：cache={cached.employ_status.name if cached else None} db={db_value}"
    )


async def test_r3b_caller_mutation_must_not_poison_cache(agent_db):
    """§7.2③ 的写路径孪生：写失败后缓存不得留下脏值。

    handler 从缓存拿对象就地改字段（§4.2 已知残留），因此在 `await` 之前
    缓存里那个对象已经是脏的。写入口必须**无条件** invalidate（`try/finally`），
    而不是「保存成功后才失效」。

    变异：把 `save_agent` 的 `finally` 改成顺序执行（save 后 invalidate）
    ⇒ 写失败时缓存仍留脏对象，本用例红。
    """
    created = await _new_agent("r3b")
    agent_id = created.id
    # ⚠️ 必须拿**缓存里那个对象**（handler 的真实路径：get_agent_by_id 命中缓存 →
    #    返回缓存实例 → 就地改字段）。若这里用 `_new_agent` 返回的实例去改，
    #    脏值只落在调用方手上那份副本上，缓存对象没被污染，用例就恒真了。
    agent = await gtAgentManager.get_agent_by_id(agent_id)
    assert agent is not None
    assert agent is gtAgentManager._agent_cache.get(agent_id), (
        "预热后 get_agent_by_id 应返回缓存实例；否则本用例守不住「共享对象被改脏」"
    )
    name_before = agent.name

    # 用唯一索引冲突制造真实写失败（team_id + employee_number）
    other = await _new_agent("r3b_other")
    agent.employee_number = other.employee_number
    agent.name = "renamed-should-not-stick"
    # 此刻缓存里那个对象已经是脏的（§4.2 已知残留），这就是失败的写入口必须障碍的场景
    assert gtAgentManager._agent_cache.get(agent_id).name == "renamed-should-not-stick"  # type: ignore[union-attr]
    with pytest.raises(Exception):
        await gtAgentManager.save_agent(agent)

    after = gtAgentManager._agent_cache.get(agent_id)
    if after is not None:
        assert after.name == name_before, (
            f"写失败后缓存留下脏值：cache.name={after.name!r} 期望 {name_before!r}"
        )
    # 用户视角：按 ID 读必须返回库里的值（从库重建）
    reread = await gtAgentManager.get_agent_by_id(agent.id)
    assert reread is not None
    assert reread.name == name_before, (
        f"写失败后按 ID 读到脏值：{reread.name!r} 期望 {name_before!r}"
    )


# ── §7.2④ 换库 + ID 复用不命中上一份库对象 ───────────────────────────────────


async def test_r4_switch_database_must_not_hit_old_object(agent_db):
    """§7.2④：换库后同 ID 复用不得命中上一份数据库的对象。

    变异：拆掉 `ormService._reset()` 里的 `clear_agent_cache()` ⇒ 本用例红。

    ⚠️ **场景覆盖有用、判别力不足**，两个属性都记着：
      - ①覆盖「换库 + ID 复用」这个**场景本身**——B5 验「换库不清缓存」、
        B13 验「在途查询不回填」，**没有一条验 ID 复用**，这条是唯一的。
      - ②判别力弱——`ormService._reset()` 自身会清，所以它验的是
        「挂点存在」，不是「在途读取跨换库」。在途那条以 B13 为准。
    不要因为②就删掉这条：删了 ID 复用这个场景就没人守了。
    """
    first = await _new_agent("alice_before")
    old_id = first.id
    await gtAgentManager.get_agent_by_id(old_id)  # 缓存住旧库对象
    assert gtAgentManager._agent_cache.get(old_id) is not None

    # 真实换库路径：走 ormService 的数据库解绑清理（生产 `_reset` 的同一挂点）
    await ormService._reset()
    db2_path = agent_db.database + ".second"
    db_tool.migrate_database(db2_path)
    db2 = AioSqliteDatabase(db2_path, pool_params={"timeout": 30})
    bind_database(db2)
    await db2.aio_connect()
    try:
        # 新库从 id=1 起，构造 ID 复用
        created = await _new_agent("小美_after")
        assert created.id == old_id, "新库应从头分配 id，构造 ID 复用"
        got = await gtAgentManager.get_agent_by_id(created.id)
        assert got is not None
        assert got.name == "小美_after", (
            f"换库后命中了上一份库的对象：name={got.name!r}（期望 '小美_after'）"
        )
    finally:
        gtAgentManager.clear_agent_cache()
        await db2.aio_close()
        unbind_database()


# ── §7.2⑤ 异步交错不把失效前旧值填回 ────────────────────────────────────────


async def test_r5_async_interleave_must_not_refill_stale(agent_db, monkeypatch):
    """§7.2⑤：读取旧值 → 写入并失效 → 旧查询不得把旧值填回缓存。

    变异：拆掉版本号检查（`_agent_cache.add_if_version` 改为直接 `_agent_cache.add`）⇒ 本用例红。
    """
    agent = await _new_agent("r5")
    agent_id = agent.id
    gtAgentManager.clear_agent_cache()  # 制造未命中，进入查库分支

    original = GtAgent.aio_get_or_none.__func__  # type: ignore[attr-defined]
    fired = {"done": False}

    async def _interleaved(cls, *a, **kw):
        stale = await original(cls, *a, **kw)  # 读到旧值（ON_BOARD）
        if not fired["done"]:
            fired["done"] = True
            # 窗口期：另一个协程完成写入 + 失效
            await gtAgentManager.batch_update_agent_status([agent_id], EmployStatus.OFF_BOARD)
        return stale  # 旧对象被返回 ⇒ 若无版本号检查会被填回缓存

    monkeypatch.setattr(
        GtAgent, "aio_get_or_none",
        classmethod(lambda cls, *a, **kw: _interleaved(cls, *a, **kw)), raising=False,
    )

    got = await gtAgentManager.get_agent_by_id(agent_id)
    assert fired["done"], "交错窗口未触发，本用例退化为无效"
    assert got is not None

    db_value = await _db_status(agent_id, agent_db)
    assert db_value == "OFF_BOARD"
    again = await gtAgentManager.get_agent_by_id(agent_id)
    assert again is not None
    assert again.employ_status == EmployStatus.OFF_BOARD, (
        f"旧查询把失效前的旧值填回了缓存：cache={again.employ_status.name} db={db_value}"
    )


# ── §7.2⑥ 日志名称解析不查库 ─────────────────────────────────────────────────


async def test_r6_get_agent_name_must_not_query_db(agent_db, monkeypatch):
    """§7.2⑥：`get_agent_name` 只读缓存，不触发任何数据库查询。

    反例侧：钉死所有查询入口，任何一次调用都让用例变红。
    **先制造缓存未命中**，否则命中缓存时断言恒真。
    """
    agent = await _new_agent("r6")
    calls: list[str] = []

    def _boom(*a, **kw):
        calls.append("hit")
        raise AssertionError("get_agent_name 不得触发任何数据库查询")

    for attr in ("get_or_none", "aio_get_or_none", "select", "get", "aio_get"):
        if hasattr(GtAgent, attr):
            monkeypatch.setattr(GtAgent, attr, _boom, raising=False)

    gtAgentManager.clear_agent_cache()
    name = gtAgentManager.get_agent_name(agent.id)
    assert name == f"unknown({agent.id})", f"未命中缓存应返回 unknown(id)，实际 {name!r}"
    assert calls == [], f"发生了 {len(calls)} 次查询调用"


# ── §7.2⑦ 保存后按 ID 读拿到新配置（热更新语义）──────────────────────────────


async def test_r7_save_agent_then_id_read_sees_new_config(agent_db):
    """§7.2⑦：`save_agent` 保存后按 ID 读须拿到新配置（热更新依赖这条）。

    变异：拆掉 `save_agent` 的 invalidate ⇒ 本用例红。
    """
    agent = await _new_agent("r7")
    await gtAgentManager.get_agent_by_id(agent.id)  # 预热缓存

    agent.allow_tools = ["t_new"]
    await gtAgentManager.save_agent(agent)

    got = await gtAgentManager.get_agent_by_id(agent.id)
    assert got is not None
    assert got.allow_tools == ["t_new"], (
        f"保存后按 ID 读仍是旧配置：allow_tools={got.allow_tools}"
    )


# ── M17 补的检查：批量读的交错检查 + 单 key 失效精确隔离 ────────────────────


async def test_r8_get_agents_by_ids_must_not_refill_stale(agent_db, monkeypatch):
    """`get_agents_by_ids` 的异步交错回填检查（M17 补的洞1）。

    M16 只给 `get_agent_by_id` 加了版本号检查，批量读仍是裸回填，
    而该函数有 11 个调用点。

    变异：把 `_agent_cache.add_many_if_version(fetched, version)` 改回不做版本号检查
    直接回填 ⇒ 本用例红。
    """
    agent = await _new_agent("r8")
    agent_id = agent.id
    gtAgentManager.clear_agent_cache()

    from peewee_async.aio_model import AioQueryMixin

    original = AioQueryMixin.aio_execute
    fired = {"done": False}

    async def _interleaved(db_self, *a, **kw):
        rows = await original(db_self, *a, **kw)
        rows_list = list(rows) if isinstance(rows, list) else rows
        # 只在「按 id 列表批量查 agents」这次插入失效
        if isinstance(rows_list, list) and len(rows_list) == 1 and not fired["done"]:
            fired["done"] = True
            await gtAgentManager.batch_update_agent_status([agent_id], EmployStatus.OFF_BOARD)
        return rows

    monkeypatch.setattr(AioQueryMixin, "aio_execute", _interleaved)

    fetched = await gtAgentManager.get_agents_by_ids([agent_id])
    assert fired["done"], "交错窗口未触发，本用例退化为无效"
    assert fetched

    db_value = await _db_status(agent_id, agent_db)
    assert db_value == "OFF_BOARD", "库应已更新（本用例的事实基准）"
    cached = gtAgentManager._agent_cache.get(agent_id)
    assert cached is None, (
        f"批量读把失效前的旧值填回了缓存：cache={cached.employ_status.name if cached else None}"
    )
    again = await gtAgentManager.get_agent_by_id(agent_id)
    assert again is not None and again.employ_status == EmployStatus.OFF_BOARD


async def test_r9_single_id_invalidate_must_not_block_other_ids(agent_db, monkeypatch):
    """单 id 失效不得连带阻断其他 id 的在途读取（版本号的精确性）。

    保证「`_agent_cache.invalidate(a1)` 只影响 a1」——否则任意一次写入都会让
    所有其他 id 的在途读取白白放弃回填，缓存命中率无谓下降。
    """
    a1 = await _new_agent("r9_a1")
    a2 = await _new_agent("r9_a2")
    gtAgentManager.clear_agent_cache()

    from peewee_async.aio_model import AioQueryMixin

    original = AioQueryMixin.aio_execute
    fired = {"done": False}

    async def _interleaved(db_self, *a, **kw):
        rows = await original(db_self, *a, **kw)
        rows_list = list(rows) if isinstance(rows, list) else rows
        if isinstance(rows_list, list) and len(rows_list) == 2 and not fired["done"]:
            fired["done"] = True
            gtAgentManager._agent_cache.invalidate(a1.id)  # 只失效 a1
        return rows

    monkeypatch.setattr(AioQueryMixin, "aio_execute", _interleaved)

    await gtAgentManager.get_agents_by_ids([a1.id, a2.id])
    assert fired["done"], "交错窗口未触发，本用例退化为无效"

    assert gtAgentManager._agent_cache.get(a1.id) is None, (
        "a1 被失效了，不应回填"
    )
    assert gtAgentManager._agent_cache.get(a2.id) is not None, (
        "a2 未被失效，应当正常回填（单 id 失效不得连带其他 id）"
    )
