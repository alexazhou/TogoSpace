-- 0022: 任务系统 v3（单棒接力多级审核工作流）数据层。
-- 设计依据：docs/design/2026-11-14-agent-task-system-v3/step2_technical.md §2.4。
--
-- 三件事，按依赖顺序：
--   ① 新建节点表 agent_task_stages（有序节点链，一行一环）；
--   ② 主表 agent_tasks 加接力游标 current_stage / current_owner_id + 待办查询索引；
--   ③ 事件表 agent_task_events 加 action_type / from_stage / to_stage（消除同态流转歧义）；
--   ④ 存量回填：把 v2 的「1 执行人 + 0/1 审核人」翻译成节点链与游标（幂等）。
--
-- 为什么要冗余 current_stage / current_owner_id 到主表：`list_tasks` 的默认口径
-- 变成「只看当前持棒人」，这是最高频查询；若每次 join 节点表算游标就无法走索引。
-- 节点表仍是真值来源，主表两列只是快照（写入一律在 taskService 的同一事务内同步）。

-- ① 节点表 ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agent_task_stages (
    id           INTEGER  PRIMARY KEY AUTOINCREMENT,
    task_id      INTEGER  NOT NULL,
    stage_index  INTEGER  NOT NULL,                 -- 0..N，连续；删节点后由 DAL 重排
    node_type    TEXT     NOT NULL,                 -- EXECUTION / REVIEW
    name         TEXT     NOT NULL DEFAULT '',
    owner_id     INTEGER  NOT NULL,
    stage_status TEXT     NOT NULL DEFAULT 'WAITING', -- WAITING / ACTIVE / APPROVED
    created_at   DATETIME NOT NULL DEFAULT (datetime('now')),
    updated_at   DATETIME NOT NULL DEFAULT (datetime('now'))
);

-- (task_id, stage_index) 唯一：唯一索引同时就是「按任务取整条链」的查询索引。
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_task_stages_task_stage ON agent_task_stages (task_id, stage_index);
-- include_upcoming=True 时反查「后续节点包含我的任务」。
CREATE INDEX IF NOT EXISTS idx_agent_task_stages_owner_status ON agent_task_stages (owner_id, stage_status);

-- ② 主表游标 ------------------------------------------------------------------
-- 允许 NULL：存量行在回填前没有游标；回填后逻辑非空（新写入一律显式赋值）。
ALTER TABLE agent_tasks ADD COLUMN current_stage INTEGER NOT NULL DEFAULT 0;
ALTER TABLE agent_tasks ADD COLUMN current_owner_id INTEGER;
CREATE INDEX IF NOT EXISTS idx_agent_tasks_team_current_owner ON agent_tasks (team_id, current_owner_id, status);

-- ③ 事件表扩展 ----------------------------------------------------------------
-- 三列都可空：历史事件（v2 期间写的）没有动作类型与节点号，NULL 表示「未登记」，不猜。
ALTER TABLE agent_task_events ADD COLUMN action_type TEXT;
ALTER TABLE agent_task_events ADD COLUMN from_stage INTEGER;
ALTER TABLE agent_task_events ADD COLUMN to_stage INTEGER;

-- ④ 存量回填（幂等：全部用 NOT EXISTS / IS NULL 兜住重跑）----------------------
-- 节点 0 = EXECUTION(assignee_id)；有 reviewer_id 时补节点 1 = REVIEW(reviewer_id)。
INSERT INTO agent_task_stages (task_id, stage_index, node_type, name, owner_id, stage_status, created_at, updated_at)
SELECT t.id, 0, 'EXECUTION', '', t.assignee_id, 'WAITING', datetime('now'), datetime('now')
FROM agent_tasks AS t
WHERE NOT EXISTS (
    SELECT 1 FROM agent_task_stages AS s WHERE s.task_id = t.id
);

INSERT INTO agent_task_stages (task_id, stage_index, node_type, name, owner_id, stage_status, created_at, updated_at)
SELECT t.id, 1, 'REVIEW', '', t.reviewer_id, 'WAITING', datetime('now'), datetime('now')
FROM agent_tasks AS t
WHERE t.reviewer_id IS NOT NULL
  AND NOT EXISTS (
    SELECT 1 FROM agent_task_stages AS s WHERE s.task_id = t.id AND s.stage_index = 1
);

-- 游标回填：DONE ⇒ 停在末节点；REVIEWING（有审核节点）⇒ 停在节点 1；其余 ⇒ 节点 0。
UPDATE agent_tasks
SET current_stage = (
        SELECT MAX(s.stage_index) FROM agent_task_stages AS s WHERE s.task_id = agent_tasks.id
    ),
    current_owner_id = (
        SELECT s.owner_id FROM agent_task_stages AS s
        WHERE s.task_id = agent_tasks.id
        ORDER BY s.stage_index DESC LIMIT 1
    )
WHERE status = 'DONE';

UPDATE agent_tasks
SET current_stage = 1,
    current_owner_id = reviewer_id
WHERE status = 'REVIEWING'
  AND reviewer_id IS NOT NULL
  AND EXISTS (
      SELECT 1 FROM agent_task_stages AS s WHERE s.task_id = agent_tasks.id AND s.stage_index = 1
  );

-- 兜底：其余状态（PENDING / TODO / IN_PROGRESS / ON_HOLD / CANCELLED，以及
-- 数据异常的「REVIEWING 但无审核节点」）一律停在节点 0、持棒人 = assignee。
UPDATE agent_tasks
SET current_stage = 0,
    current_owner_id = assignee_id
WHERE current_owner_id IS NULL;

-- 局部状态回填：必须放在游标回填之后（依赖 current_stage）。
-- ① DONE：全部节点 APPROVED（工作流已结束）。
UPDATE agent_task_stages
SET stage_status = 'APPROVED'
WHERE task_id IN (SELECT id FROM agent_tasks WHERE status = 'DONE');

-- ② 非 DONE 且停在节点 1（即 REVIEWING）：节点 0 已通过、节点 1 持棒中。
UPDATE agent_task_stages
SET stage_status = 'APPROVED'
WHERE stage_index = 0
  AND task_id IN (SELECT id FROM agent_tasks WHERE status <> 'DONE' AND current_stage = 1);

UPDATE agent_task_stages
SET stage_status = 'ACTIVE'
WHERE stage_index = 1
  AND task_id IN (SELECT id FROM agent_tasks WHERE status <> 'DONE' AND current_stage = 1);

-- ③ 非 DONE 且停在节点 0：节点 0 持棒中（节点 1 保持建表默认 WAITING）。
--    注：PENDING 在 v3 语义里「棒还没发出」，但本迁移按 step2 §2.4 的既有口径
--    统一置 ACTIVE（与 current_stage=0 一致）；是否区分 PENDING 留给后续单子，不在此处改判。
UPDATE agent_task_stages
SET stage_status = 'ACTIVE'
WHERE stage_index = 0
  AND task_id IN (SELECT id FROM agent_tasks WHERE status <> 'DONE' AND current_stage = 0);
