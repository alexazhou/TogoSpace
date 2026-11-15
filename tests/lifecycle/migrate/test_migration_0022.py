"""0022 迁移（任务系统 v3 数据层）的生命周期测试（#227 判据 M1）。

本迁移干四件事：建 `agent_task_stages` 节点表、主表加 `current_stage` /
`current_owner_id` 游标、事件表加 `action_type` / `from_stage` / `to_stage`、
把 v2 存量任务回填成节点链。本文件钉住：

1. **8 种 v2 存量形态回填全对**（逐行比对节点、局部状态、游标、持棒人），
   包括数据异常形态「REVIEWING 但 reviewer_id 为空」——必须落到单节点兜底，
   不能因为少一条审核节点就在运行期报 `non_final_stage_cannot_done`；
2. **历史事件不猜**：v2 期间写的事件三列为 NULL，迁移不填假值；
3. **幂等**：已记账后再跑不报错、不改节点与游标、`applied == []`；
4. **全新库一次到位**：空库跑完所有迁移即有节点表与三列，不依赖回填；
5. **索引就在位**（`(task_id, stage_index)` 唯一 + 待办/反查索引）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import db

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "assets/migrate"
MIGRATION_0022 = "0022_add_agent_task_stages.sql"


def _migrate_up_to_0021(db_path: Path) -> None:
    """迁移到 0021 为止（`up_to` 是前缀开区间，22 表示 < 0022）。"""
    db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR, up_to="22")


# (task_id, status, assignee_id, reviewer_id) —— 覆盖 v2 的全部存量形态。
# id=4 是刻意造的数据异常：状态 REVIEWING 但没人可审（reviewer_id 为空）。
_SEEDS = [
    (1, "TODO", 11, 21),  # 已指派审核人但还没开工
    (2, "IN_PROGRESS", 12, 21),  # 执行人干着
    (3, "REVIEWING", 13, 22),  # 审核人审着
    (4, "REVIEWING", 14, None),  # 异常：REVIEWING 但无审核人
    (5, "DONE", 15, 23),  # 两级走完
    (6, "DONE", 16, None),  # 无审核人直接完成
    (7, "CANCELLED", 17, 24),  # 取消（审核人没轮上）
    (8, "PENDING", 18, 25),  # 依赖未就绪
]


def _seed_pre_0022_rows(db_path: Path) -> None:
    """在 0021 的 schema 上插入 v2 存量任务与历史事件。"""
    with sqlite3.connect(str(db_path)) as conn:
        conn.executemany(
            "INSERT INTO agent_tasks"
            " (id, team_id, title, assignee_id, creator_id, reviewer_id, status)"
            " VALUES (?, 1, ?, ?, 99, ?, ?)",
            [(task_id, f"v2-{task_id}", assignee, reviewer, status)
             for task_id, status, assignee, reviewer in _SEEDS],
        )
        # v2 期间写的历史事件：迁移后三列必须仍为 NULL（不猜动作与节点）
        conn.execute(
            "INSERT INTO agent_task_events (task_id, actor_id, from_status, to_status, content)"
            " VALUES (3, 13, 'IN_PROGRESS', 'REVIEWING', 'v2 提交验收')"
        )


def _stages(db_path: Path) -> dict[int, list[tuple]]:
    """{task_id: [(stage_index, node_type, owner_id, stage_status), ...]} 按序号升序。"""
    with sqlite3.connect(str(db_path)) as conn:
        rows = list(
            conn.execute(
                "SELECT task_id, stage_index, node_type, owner_id, stage_status"
                " FROM agent_task_stages ORDER BY task_id, stage_index"
            )
        )
    out: dict[int, list[tuple]] = {}
    for task_id, index, node_type, owner, status in rows:
        out.setdefault(task_id, []).append((index, node_type, owner, status))
    return out


def _cursors(db_path: Path) -> dict[int, tuple]:
    """{task_id: (current_stage, current_owner_id)}"""
    with sqlite3.connect(str(db_path)) as conn:
        return {
            row[0]: (row[1], row[2])
            for row in conn.execute("SELECT id, current_stage, current_owner_id FROM agent_tasks")
        }


def _indexes(db_path: Path, table: str) -> dict[str, bool]:
    """{索引名: 是否唯一}"""
    with sqlite3.connect(str(db_path)) as conn:
        return {
            row[1]: bool(row[2])
            for row in conn.execute(f"PRAGMA index_list('{table}')")
        }


EX = "EXECUTION"
RV = "REVIEW"
AP = "APPROVED"
AC = "ACTIVE"
WA = "WAITING"

EXPECTED_STAGES = {
    1: [(0, EX, 11, AC), (1, RV, 21, WA)],
    2: [(0, EX, 12, AC), (1, RV, 21, WA)],
    3: [(0, EX, 13, AP), (1, RV, 22, AC)],
    4: [(0, EX, 14, AC)],  # 异常形态：只有执行节点，不能凭空造审核节点
    5: [(0, EX, 15, AP), (1, RV, 23, AP)],
    6: [(0, EX, 16, AP)],
    7: [(0, EX, 17, AC), (1, RV, 24, WA)],
    8: [(0, EX, 18, AC), (1, RV, 25, WA)],
}

EXPECTED_CURSORS = {
    1: (0, 11),
    2: (0, 12),
    3: (1, 22),
    4: (0, 14),
    5: (1, 23),
    6: (0, 16),
    7: (0, 17),
    8: (0, 18),
}


def test_0022_backfills_every_v2_shape(tmp_path: Path) -> None:
    """存量库升到 0022：8 种 v2 形态的节点链、局部状态、游标、持棒人逐项对。"""
    db_path = tmp_path / "test.db"
    _migrate_up_to_0021(db_path)
    _seed_pre_0022_rows(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        assert not list(
            conn.execute("SELECT name FROM sqlite_master WHERE name = 'agent_task_stages'")
        ), "0021 及以前不该有节点表"

    applied = db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR)

    # 0022 是此刻唯一「待应用」的迁移；后续新增的迁移会跟在后面一起被应用，
    # 故断言「首条是 0022」+「其后都是更晚的迁移」，既不放松也不必每来一条迁移改一次。
    assert applied[0] == MIGRATION_0022, applied
    assert all(name > MIGRATION_0022 for name in applied[1:]), applied

    assert _stages(db_path) == EXPECTED_STAGES
    assert _cursors(db_path) == EXPECTED_CURSORS


def test_0022_leaves_history_events_untouched(tmp_path: Path) -> None:
    """历史事件的三列留 NULL：迁移不猜「这条事件是交棒还是打回、走了哪两个节点」。"""
    db_path = tmp_path / "test.db"
    _migrate_up_to_0021(db_path)
    _seed_pre_0022_rows(db_path)

    db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR)

    with sqlite3.connect(str(db_path)) as conn:
        rows = list(
            conn.execute(
                "SELECT action_type, from_stage, to_stage FROM agent_task_events"
            )
        )
    assert rows == [(None, None, None)]


def test_0022_creates_indexes(tmp_path: Path) -> None:
    """`(task_id, stage_index)` 唯一（兼做整链查询索引），另有待办与反查索引。"""
    db_path = tmp_path / "test.db"
    db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR)

    assert _indexes(db_path, "agent_task_stages") == {
        "idx_agent_task_stages_task_stage": True,
        "idx_agent_task_stages_owner_status": False,  # 反查索引，允许重复（同一人多个节点）
    }
    assert "idx_agent_tasks_team_current_owner" in _indexes(db_path, "agent_tasks")


def test_0022_is_not_reapplied_when_ledgered(tmp_path: Path) -> None:
    """已记账后再跑：不报错、`applied == []`、节点与游标一个不变。"""
    db_path = tmp_path / "test.db"
    _migrate_up_to_0021(db_path)
    _seed_pre_0022_rows(db_path)
    db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR)
    stages_before = _stages(db_path)
    cursors_before = _cursors(db_path)

    assert db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR) == []

    assert _stages(db_path) == stages_before
    assert _cursors(db_path) == cursors_before


def test_0022_replay_protection_comes_from_ledger(tmp_path: Path) -> None:
    """钉住事实：重放保护是账本，不是 SQL 里的 NOT EXISTS。

    手工删掉账本行再跑一次：脚本自带的 `BEGIN;` 之后，第一条
    `ALTER TABLE ... ADD COLUMN` 就会撞 `duplicate column name`；`db` 层把该错误当
    「可忽略」并重新记账，而**整个脚本因事务回滚而一句都没落下**（#253 M-1 之后的
    真实机制是「0022 自带 BEGIN/COMMIT + apply_migration 兜底回滚」，不再是早前
    注释里写的「executescript 已回滚」那种说法——那时建表其实已提交，只是因为
    ALTER 排在回填之前才没暴露）。也就意味着 ④ 的回填不会再跑一次。
    这条用例存在的价值：**别把 0022 当成可重放脚本**（例如不能拿它去
    「补跑」一个半旧库），也不能因为 SQL 里写了 `NOT EXISTS` 就以为重跑安全。
    """
    db_path = tmp_path / "test.db"
    _migrate_up_to_0021(db_path)
    _seed_pre_0022_rows(db_path)
    db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR)
    stages_before = _stages(db_path)

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("DELETE FROM _migrations WHERE name = ?", (MIGRATION_0022,))

    # 不抛异常（duplicate column 被 db 层当可忽略），并重新记账
    assert db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR) == [MIGRATION_0022]
    assert _stages(db_path) == stages_before
    with sqlite3.connect(str(db_path)) as conn:
        names = [row[0] for row in conn.execute("SELECT name FROM _migrations")]
    assert names.count(MIGRATION_0022) == 1


def test_0022_fresh_db_has_stages_table(tmp_path: Path) -> None:
    """全新库一次到位：空库跑完全部迁移即有节点表与事件三列（不靠回填）。"""
    db_path = tmp_path / "fresh.db"
    applied = db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR)

    assert MIGRATION_0022 in applied
    with sqlite3.connect(str(db_path)) as conn:
        stage_columns = [row[1] for row in conn.execute("PRAGMA table_info(agent_task_stages)")]
        event_columns = [row[1] for row in conn.execute("PRAGMA table_info(agent_task_events)")]
        cursor_columns = [row[1] for row in conn.execute("PRAGMA table_info(agent_tasks)")]
        assert list(conn.execute("SELECT COUNT(*) FROM agent_task_stages"))[0][0] == 0
    assert stage_columns == [
        "id", "task_id", "stage_index", "node_type", "name",
        "owner_id", "stage_status", "created_at", "updated_at",
    ]
    assert {"action_type", "from_stage", "to_stage"} <= set(event_columns)
    assert {"current_stage", "current_owner_id"} <= set(cursor_columns)


def test_0022_header_does_not_claim_replay_safety() -> None:
    """头部与 ④ 段的口径必须一致：**不许出现「幂等」字样**（#229 D3）。

    来历：#227 交付时头部第 8 行还写着「④ 存量回填…（幂等）」，而 ④ 段前已经写明
    「本文件级不可重放、重放保护来自账本」——两处自相矛盾，验收人 38 记档。
    这条把「别再写回去」变成机器判据：只要有人把「幂等」写回注释，它就红。
    """
    text = (MIGRATIONS_DIR / MIGRATION_0022).read_text(encoding="utf-8")

    assert "幂等" not in text, (
        "0022 注释里又出现了「幂等」：本迁移文件级不可重放，重放保护来自迁移账本"
    )
    assert "不可重放" in text, "头部必须明确写出「文件级不可重放」"
    assert "账本" in text, "必须点明真正的重放保护来源是迁移账本"


# ---------------------------------------------------------------------------
# M-1 / M-2（#253 review_notes）：0022 的原子执行与回填时间戳口径
# ---------------------------------------------------------------------------

def _table_exists(db_path: Path, name: str) -> bool:
    with sqlite3.connect(str(db_path)) as conn:
        return bool(list(conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name = ?", (name,)
        )))


def _columns(db_path: Path, table: str) -> list[str]:
    with sqlite3.connect(str(db_path)) as conn:
        return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def _ledger_0022_count(db_path: Path) -> int:
    with sqlite3.connect(str(db_path)) as conn:
        return list(conn.execute(
            "SELECT COUNT(*) FROM _migrations WHERE name = ?", (MIGRATION_0022,)
        ))[0][0]


def _injected_0022(tmp_path: Path) -> Path:
    """把真实 0022 复制一份，在 ④ 第一条回填 INSERT 之后插一条必然失败的语句。

    为什么不改真实文件：真实 0022 是交付物，测试只读它；而「中途失败」必须让脚本
    真的执行到一半才抛错。注入的是一条 `owner_id` 为 NULL 的 INSERT（该列 NOT NULL）
    ⇒ `sqlite3.IntegrityError`（不是 `duplicate column name` 那类「可忽略错误」，
    所以不会被 `_is_ignorable_migration_error` 吞掉）。
    """
    sql = (MIGRATIONS_DIR / MIGRATION_0022).read_text(encoding="utf-8")
    anchor = (
        "INSERT INTO agent_task_stages (task_id, stage_index, node_type, name, owner_id,"
        " stage_status, created_at, updated_at)"
    )
    end = sql.index(";", sql.index(anchor)) + 1
    boom = (
        "\nINSERT INTO agent_task_stages (task_id, stage_index, node_type, owner_id)"
        " VALUES (999, 9, 'EXECUTION', NULL);\n"
    )
    out_dir = tmp_path / "migrate_injected"
    out_dir.mkdir()
    target = out_dir / MIGRATION_0022
    target.write_text(sql[:end] + boom + sql[end:], encoding="utf-8")
    return target


def test_0022_midway_failure_rolls_back_whole_script(tmp_path: Path) -> None:
    """M-1：④ 回填阶段抛错 ⇒ 整份 0022 原子回滚（表 / 新增列 / 回填行 / 账本全无残留）。

    原缺陷（review_notes M-1）：`conn.executescript()` 会先隐式 COMMIT 掉外层事务、
    再逐条自动提交执行脚本，而 0022 自身没有 `BEGIN;`/`COMMIT;`。于是回填中途报错时，
    ① 建表与 ②③ 的 6 条 `ALTER` 已经落库；下次启动重跑 0022 会在第一条 ALTER 上撞
    `duplicate column name`，被 `_is_ignorable_migration_error` 当作可忽略错误直接记账
    ⇒ **回填被永久静默跳过**。所以本用例判两段：失败后无残留 + 去掉注入后能正常回填。
    """
    db_path = tmp_path / "test.db"
    _migrate_up_to_0021(db_path)
    _seed_pre_0022_rows(db_path)
    injected = _injected_0022(tmp_path)

    with db.connect_sqlite(db_path) as conn:
        db.ensure_migrations_table(conn)
        with pytest.raises(sqlite3.IntegrityError):
            db.apply_migration(conn, injected)

    assert not _table_exists(db_path, "agent_task_stages"), (
        "回填失败后节点表残留 ⇒ 0022 不是原子执行（建表已落库）"
    )
    assert not ({"current_stage", "current_owner_id"} & set(_columns(db_path, "agent_tasks"))), (
        "回填失败后主表游标列残留 ⇒ ② 的 ALTER 已落库"
    )
    assert not ({"action_type", "from_stage", "to_stage"} & set(_columns(db_path, "agent_task_events"))), (
        "回填失败后事件表三列残留 ⇒ ③ 的 ALTER 已落库"
    )
    assert _ledger_0022_count(db_path) == 0, "失败后不该记账"

    # 去掉注入（用真实文件重跑）：必须能正常回填；否则就是「半迁移 + 静默跳过」
    applied = db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR)
    assert MIGRATION_0022 in applied, applied
    assert _stages(db_path) == EXPECTED_STAGES, "半迁移后重跑没有回填：回填被静默跳过了"


def test_0022_backfill_timestamps_stay_in_local_time(tmp_path: Path) -> None:
    """M-2：回填的 `created_at` / `updated_at` 必须是**本地时间**。

    原缺陷（review_notes M-2，原为待讨论、按建议方案实现）：`0015` 约定回填用
    `datetime('now', 'localtime')`（与 ORM 的 `datetime.datetime.now()` 同口径），
    而 0022 用的是 `datetime('now')`（UTC）⇒ 与 `gtAgentTaskStageManager` 后续写入
    相差一个时区（本机 +8 小时 = 28800 秒）。
    判据用「与当前本地时间相差 < 120 秒」：时区错位是 28800 秒量级，两者不会混淆。
    若本机时区恰为 UTC（偏移 ≈ 0）则本条无法鉴别，跳过并说明。
    """
    db_path = tmp_path / "test.db"
    _migrate_up_to_0021(db_path)
    _seed_pre_0022_rows(db_path)

    with sqlite3.connect(str(db_path)) as conn:
        tz_offset = list(conn.execute(
            "SELECT (julianday('now','localtime') - julianday('now')) * 86400"
        ))[0][0]
    if abs(tz_offset) < 60:
        pytest.skip("本机时区为 UTC，时间漂移无法鉴别回填时区")

    db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR)

    with sqlite3.connect(str(db_path)) as conn:
        created_drift, updated_drift = list(conn.execute(
            "SELECT MAX(ABS(julianday(created_at) - julianday('now','localtime'))),"
            "       MAX(ABS(julianday(updated_at) - julianday('now','localtime')))"
            " FROM agent_task_stages"
        ))[0]

    tolerance = 120 / 86400  # 120 秒，单位：儒略日
    assert created_drift is not None and created_drift < tolerance, (
        f"created_at 与本地时间相差 {created_drift * 86400:.0f} 秒（回填用了 UTC 的 datetime('now')？）"
    )
    assert updated_drift is not None and updated_drift < tolerance, (
        f"updated_at 与本地时间相差 {updated_drift * 86400:.0f} 秒（回填用了 UTC 的 datetime('now')？）"
    )


def test_0022_ignorable_error_leaves_no_half_migration(tmp_path: Path) -> None:
    """M-1 后半：**被当作「可忽略」的错误**也不得留下半迁移残留。

    这条专门钉 `apply_migration` 的兜底回滚（而不是 0022 自带的 BEGIN/COMMIT）：
    构造一个「主表已有 current_stage（半迁移）」的库再跑 0022 ⇒ 第一条
    `ALTER TABLE ... ADD COLUMN current_stage` 撞 `duplicate column name`，被
    `_is_ignorable_migration_error` 当作可忽略并直接记账。此时连接里还开着脚本的
    事务（`BEGIN;` 之后失败），若不在记账**之前**先回滚，`with conn:` 会把 ① 建好的
    节点表连同索引一起提交 —— 于是「账本说已应用、库里只有半个 0022」。
    """
    db_path = tmp_path / "test.db"
    _migrate_up_to_0021(db_path)
    _seed_pre_0022_rows(db_path)
    # 半迁移形态：② 的第一条 ALTER 已在库里（其余什么都没做）
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("ALTER TABLE agent_tasks ADD COLUMN current_stage INTEGER NOT NULL DEFAULT 0")

    applied = db.migrate_database(db_path, migrations_dir=MIGRATIONS_DIR)

    assert MIGRATION_0022 in applied, "可忽略错误仍应记账（既有契约）"
    assert not _table_exists(db_path, "agent_task_stages"), (
        "可忽略错误后节点表残留 ⇒ 记账前没回滚，把半个 0022 提交了"
    )
    assert "action_type" not in _columns(db_path, "agent_task_events"), (
        "可忽略错误后事件表三列残留 ⇒ 同上"
    )
