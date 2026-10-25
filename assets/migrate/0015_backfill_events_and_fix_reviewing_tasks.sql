-- 0015: 任务系统 v2 存量数据处置。
-- （本文件 = 原 0016 + 原 0017 逐句合并；语句与行为不变，顺序：结转 → 订正。）
--
-- ① 存量历史任务数据结转。
-- 对 v2 之前、且尚无任何事件行的老任务，补一条基线快照事件：
--   from_status = NULL、to_status = 该任务当前状态、actor_id = -2 (SpecialAgent.SYSTEM)。
-- 范围（方案1）：result / block_reason 任一列非空即结转（含全部在途状态）；两列皆空不插。
-- content 格式 = 固定前缀 `【系统迁移自动生成】` + 逐档原文（逐档只决定「取哪一列」）：
--              终态(DONE/CANCELLED)→result（空则回退 block_reason）；
--              ON_HOLD→block_reason（空则回退 result）；
--              在途(TODO/PENDING/IN_PROGRESS/REVIEWING)→取非空那一列（两列都非空时 result 优先）。
-- 幂等：以「该任务是否已有任何事件行」为判据，只对无事件的老任务补快照（混合库不漏、不重）。
-- 时间：created_at/updated_at 显式写迁移执行时刻（本地时区），不依赖 DDL 默认（默认取 UTC）。
-- 副作用（照记）：老任务派生的「最近一次说明」/「当前搁置原因」会连带该前缀，暂不剥离。
INSERT INTO agent_task_events (task_id, actor_id, from_status, to_status, content, created_at, updated_at)
SELECT
    t.id,
    -2,
    NULL,
    t.status,
    '【系统迁移自动生成】' || CASE
        WHEN t.status = 'ON_HOLD' THEN
            CASE
                WHEN COALESCE(t.block_reason, '') <> '' THEN t.block_reason
                ELSE COALESCE(t.result, '')
            END
        ELSE
            CASE
                WHEN COALESCE(t.result, '') <> '' THEN t.result
                ELSE COALESCE(t.block_reason, '')
            END
    END,
    datetime('now', 'localtime'),
    datetime('now', 'localtime')
FROM agent_tasks AS t
WHERE NOT EXISTS (
        SELECT 1 FROM agent_task_events AS e WHERE e.task_id = t.id
    )
    AND (COALESCE(t.result, '') <> '' OR COALESCE(t.block_reason, '') <> '');

-- ② 存量数据订正 —— 修复「REVIEWING 且 reviewer_id 为空」的老任务。
-- 背景：#64 去掉了「creator 隐式接管验收人」的兜底后，这类老任务卡死
--       （assignee 既不能通过、也不能打回，仅 creator 可取消）。
-- 处置（Operator 裁定 (b) 数据订正）：把旧代码的「creator 兜底验收人」语义落成显式数据，
--       reviewer_id = creator_id。
-- 范围：仅 status = 'REVIEWING' 且 reviewer_id IS NULL 且 creator_id IS NOT NULL 的行。
--       其余行（REVIEWING 已有 reviewer / 非 REVIEWING / 终态）一律不动。
-- 幂等：订正后 reviewer_id 非空 ⇒ 不再匹配；重复执行 0 增量。
-- 残余边界：creator_id 为 NULL 的行不订正（防御性条件；0009 schema 中 creator_id INTEGER NOT NULL
--           ⇒ 该情形结构上不可能出现）。
-- 另注（阿张 #69 复核 info，非回归）：若 creator_id == assignee_id 且 reviewer_id 为空，订正后
--           reviewer_id 即 assignee 自己 ⇒ 该单进入「自审」。此与旧代码「creator 兜底验收人」
--           语义一致（creator 兜底时同样是自审），本迁移不改变该行为，也不加
--           `assignee_id <> creator_id` 过滤（否则该行会退回卡死态）。
-- 注意：本迁移只碰任务表 agent_tasks.reviewer_id，不涉及部门域的 manager_id。
UPDATE agent_tasks
SET reviewer_id = creator_id
WHERE status = 'REVIEWING'
  AND reviewer_id IS NULL
  AND creator_id IS NOT NULL;
