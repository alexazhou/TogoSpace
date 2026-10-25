-- 0014: 任务系统 v2 数据层（一）—— 新增不可变事件表 + 任务域字段改名。
-- （本文件 = 原 0014 + 原 0015 逐句合并；语句与行为不变，顺序：建表 → 改列。）
--
-- ① 新增不可变事件表 agent_task_events（append-only）。
--    每条事件 = 一次操作：状态流转 / 评论 / 存量基线快照。
--    语义角色由 from_status / to_status 的 NULL 组合唯一决定（不落 event_type 字段）。
CREATE TABLE IF NOT EXISTS agent_task_events (
    id          INTEGER  PRIMARY KEY AUTOINCREMENT,
    task_id     INTEGER  NOT NULL,
    actor_id    INTEGER  NOT NULL,                   -- 操作人 Agent ID；系统自动操作为 -2 (SpecialAgent.SYSTEM)
    from_status TEXT,                                -- 变更前状态；日常评论事件为 NULL
    to_status   TEXT,                                -- 变更后状态；日常评论事件为 NULL
    content     TEXT     NOT NULL DEFAULT '',        -- 唯一文本载荷：操作注释 / 评论正文
    created_at  DATETIME NOT NULL DEFAULT (datetime('now')),
    updated_at  DATETIME NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_agent_task_events_task  ON agent_task_events (task_id, id);
CREATE INDEX IF NOT EXISTS idx_agent_task_events_actor ON agent_task_events (actor_id);

-- ② 任务域字段改名 manager_id -> reviewer_id（避免与「部门主管」概念混淆）。
--    注意：仅任务表 agent_tasks；部门模型 GtDept.manager_id（部门主管）保持完全不变。
--    RENAME COLUMN 非幂等，只执行一次（迁移框架按 _migrations 记录，不重复执行）。
ALTER TABLE agent_tasks RENAME COLUMN manager_id TO reviewer_id;
