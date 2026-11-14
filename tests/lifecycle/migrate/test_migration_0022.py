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

    手工删掉账本行再跑一次：第一条 `ALTER TABLE ... ADD COLUMN` 就会撞
    `duplicate column name`，`db` 层把该错误当「可忽略」并重新记账，整段
    `executescript` 已回滚 —— 也就意味着 ④ 的回填不会再跑一次。
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
