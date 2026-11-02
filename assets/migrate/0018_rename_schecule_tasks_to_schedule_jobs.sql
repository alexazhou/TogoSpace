-- 0018: 调度任务表 schecule_tasks → schedule_jobs，顺带修正 schecule 拼写，字段 task_type → job_type。
--
-- 与协作任务（agent_tasks）无关：本迁移只动 scheduler 投递的「执行单元」这一侧。
--
-- ① 必须显式 BEGIN; … COMMIT;（见下方「事务」）。
-- ② ALTER TABLE RENAME 不会改索引名（SQLite 实测保留旧名 idx_schecule_tasks_agent_status），
--    所以索引必须显式 DROP + CREATE。
-- ③ 枚举值名一律不变（AgentJobType.TODO_TASK 保留），故无任何存量数据 UPDATE。
--
-- 事务：`db.py:apply_migration` 用 `conn.executescript(sql)` 执行本文件，而
-- `executescript` 进入时会先**隐式 COMMIT** 当前事务，`with conn:` 的异常回滚兜不住 DDL。
-- 若本文件不带显式事务且中途失败（例如表已改名、改列失败），库会停在
-- 「schedule_jobs 存在 + job_type 不存在」的脏状态；重跑时第一条 ALTER 就
-- `no such table`，而 `_is_ignorable_migration_error` 只放行
-- `duplicate column name` / `already exists`，不放行 `no such table`
-- ⇒ 迁移永不记账、**每次启动都失败**。故此处显式 BEGIN/COMMIT 保证整体回滚。
-- 覆盖该行为的测试：tests/unit/test_migration_0018.py。

BEGIN;

ALTER TABLE schecule_tasks RENAME TO schedule_jobs;

DROP INDEX IF EXISTS idx_schecule_tasks_agent_status;
CREATE INDEX IF NOT EXISTS idx_schedule_jobs_agent_status ON schedule_jobs(agent_id, status);

ALTER TABLE schedule_jobs RENAME COLUMN task_type TO job_type;

COMMIT;
