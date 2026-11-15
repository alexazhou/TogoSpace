# `feat/task-system-v3` Code Review 清单

- **评审分支**：`feat/task-system-v3`（worktree: `dev_storage_root/workspace/软件研发团队/agent/李哥/wt_v3`，基于 `0.5_dev` 领先 8 个 commit）
- **当前测试基线**：`test_migration_0022.py`、`test_task_workflow_v3/test.py`、`test_task_service.py` 共 150 passed
- **文档用途**：汇总本次 review 发现的问题与修改建议，逐项讨论确认后直接在本分支落地修改。

---

## 一、Migrate 部分

### [M-1] P0：`0022` 迁移脚本未显式开启事务，中途失败会导致「半迁移 + 后续静默跳过回填」

- **状态**：已确认方案（待修改）
- **确认口径**：
  1. `0022_add_agent_task_stages.sql` 首尾加 `BEGIN;` / `COMMIT;` 保证整份脚本原子执行；
  2. `src/db.py` 的 `apply_migration` 在外围对异常与执行结束做连接状态兜底（`if conn.in_transaction: conn.rollback()`），确保每个 migration 执行/异常处理后连接一定回到非事务状态。
- **涉及文件**：
  - `assets/migrate/0022_add_agent_task_stages.sql`
  - `src/db.py`（L135-L142）
  - `tests/lifecycle/migrate/test_migration_0022.py`（L194-L195）
- **问题描述**：
  - `src/db.py` 的 `apply_migration` 在 `with conn:` 内调用 `conn.executescript(sql)`。
  - 在 Python `sqlite3` 中，`executescript()` 会**先隐式 `COMMIT` 当前事务**，然后以逐条自动提交的方式执行脚本内容，**不受外层 `with conn:` 包裹**。
  - `0022_add_agent_task_stages.sql` 自身没有 `BEGIN;` ... `COMMIT;`。一旦第 ④ 段存量回填（L50-L116）中途报错，前面的 `CREATE TABLE` 和 `ALTER TABLE` 已经提交且不会回滚；`_migrations` 不会记账。下次启动重跑 `0022` 时，L35 第一条 `ALTER TABLE` 就会触发 `duplicate column name`，被 `_is_ignorable_migration_error` 当成可忽略错误直接写入 `_migrations` 账本——**导致回填被永久静默跳过**。
  - 此外，`test_migration_0022.py` L194-L195 注释「整段 executescript 已回滚」也不准确（只是因为 `ALTER` 刚好在回填语句之前失败，`CREATE TABLE IF NOT EXISTS` 实际已提交）。
- **建议方案**：
  - 在 `0022_add_agent_task_stages.sql` 首尾显式包裹 `BEGIN;` 和 `COMMIT;`（SQLite 支持事务内 DDL，失败时整份脚本完整回滚）。
  - 同步更新 `test_migration_0022.py` L194-L195 注释，并可补一条「回填阶段抛错时整段 0022 回滚（表与列均未残留）」的测试。

---

### [M-2] P2：迁移回填时间戳时区与项目约定不一致

- **状态**：待讨论
- **涉及文件**：
  - `assets/migrate/0022_add_agent_task_stages.sql`（L24-L25、L51、L58）
  - 参考：`assets/migrate/0015_backfill_events_and_fix_reviewing_tasks.sql`（L13、L33-L34）
- **问题描述**：
  - `0015` 迁移明确约定回填的 `created_at` / `updated_at` 使用 `datetime('now', 'localtime')`（与 Python ORM 的 `datetime.datetime.now()` 本地时间口径一致）。
  - `0022` 在建表默认与 `INSERT ... SELECT` 回填中均使用 `datetime('now')`（UTC），与 `gtAgentTaskStageManager.py` 后续写入的 `datetime.datetime.now()` 存在时区混用（差 8 小时）。
- **建议方案**：
  - 回填 `INSERT` 语句的 `created_at` / `updated_at` 改为 `datetime('now', 'localtime')`（建表 `DEFAULT` 可保持或同步调整，主要以显式插入值为准）。

---

## 二、Service / DAL 部分

### [S-1] P0：`update_task(..., reviewer_id=...)` 与 `clear_fields=["reviewer_id"]` 未同步节点链与持棒人

- **状态**：已确认方案（待修改）
- **确认口径**：保留 v2 接口，在内部同步节点表：
  1. 单审核/无审核任务（v2 形态）：改 `reviewer_id` 或 `clear_fields=["reviewer_id"]` 时，在同一事务内同步 `agent_task_stages`（修改/追加/删除唯一的 `REVIEW` 节点）以及 `current_owner_id`（若当前处于 `REVIEWING`）；
  2. 多级任务（节点数 > 2 或 `REVIEW` 节点 > 1）：拒绝 `reviewer_id=` / `clear_fields=["reviewer_id"]`，提示使用 `reassign_stage` 指定具体节点序号。
- **涉及文件**：
  - `src/service/taskService.py`：`_handle_reviewer_change`（L584-L744）、`_update_reviewer_with_event`（L555-L581）
- **问题描述**：
  - `_update_reviewer_with_event` 目前只更新 `agent_tasks.reviewer_id`，**完全没有同步 `agent_task_stages` 和 `current_owner_id`**。
  - 典型坏路径：
    1. **REVIEWING 状态下换验收人**：旧验收人调用 `update_task(reviewer_id=新人)` 后，节点 1 的 `owner_id` 和 `task.current_owner_id` 仍是旧人 -> 新验收人调 `DONE` / `advance` / `reject` 全部被拒（`permission_denied` / `not_current_stage_owner`），而 `mine=True` 待办仍挂在旧验收人名下。
    2. **撤销验收人（`clear_fields=["reviewer_id"]`）**：节点链里仍残留 `REVIEW` 节点 -> 执行人后续置 `DONE` 时被 `_plan_for_compat_transition` 拦截报 `non_final_stage_cannot_done`。
    3. **给无验收人任务补设 `reviewer_id`**：节点链仍只有 1 个 `EXECUTION` 节点 -> 提交 `REVIEWING` 时同样报 `non_final_stage_cannot_done`。
- **建议方案**：
  - 明确 v2 `reviewer_id` 变更接口与 v3 节点链的同步规则（例如：单审核节点任务改 `reviewer_id` 同步改对应 `REVIEW` 节点与 `current_owner_id`；`clear_fields=["reviewer_id"]` 在未开始审核前同步移除对应 `REVIEW` 节点；无审核节点补设 `reviewer_id` 时同步追加 `REVIEW` 节点；多级 `REVIEW` 节点任务是否允许走 `reviewer_id` 还是引导用 `reassign_stage`）。

---

### [S-2] P0：`workflow_action="advance"` 绕过了 `PENDING` 依赖检查与 `ON_HOLD` 状态闸门

- **状态**：待讨论
- **涉及文件**：
  - `src/service/taskService.py`：`_apply_workflow_action`（L1045-L1159）
- **问题描述**：
  - `_apply_workflow_action` 只检查了「是否终态」和「`_is_holder`」，没有检查当前状态是否为 `PENDING`（依赖未满足）或 `ON_HOLD`（搁置中）。
  - 当任务因 `depends_on` 未完成处于 `PENDING` 时，节点 0 持棒人（执行人）直接调用 `update_task(workflow_action="advance")` 可以**完全无视未完成依赖，把任务推进到 `REVIEWING` 甚至直接 `DONE`**。
  - 任务处于 `ON_HOLD` 时同理，可不经恢复（`IN_PROGRESS`）直接交棒推进。
- **建议方案**：
  - `task.status == TaskStatus.ON_HOLD` 时拒绝 `advance` / `reject`（提示先恢复到在途状态）。
  - `task.status == TaskStatus.PENDING` 时，复用依赖检查逻辑：若仍有未 `DONE` 的前置依赖则拒绝并报错 `dependency_not_met`（若依赖均已 `DONE` 可按允许推进或要求先流转）。

---

### [S-3] P1：`_apply_stage_update` 缺少事务包裹（违反 DAL 约定）

- **状态**：待讨论
- **涉及文件**：
  - `src/service/taskService.py`：`_apply_stage_update`（L1162-L1343）
  - 参考：`src/dal/db/gtAgentTaskStageManager.py`（L3-L5）
- **问题描述**：
  - `gtAgentTaskStageManager` 明确约定「本模块不自行开事务，假定调用方已在同一事务内」。
  - 但 `_apply_stage_update` 中的三个分支（`append_stages`、`remove_stage_index`、`reassign_stage`）都直接依次调用 DAL 改节点表、改主表 `reviewer_id`/`current_owner_id`、写事件表，**没有用 `async with atomic():` 包裹**。一旦其中某步失败，节点链索引重排、主表快照与事件时间线会出现部分写入。
- **建议方案**：
  - 三个分支的多步写库操作统一包进 `async with atomic():`，事件推送 `_publish_event_appended` 保持在事务块外。

---

### [S-4] P1：`append_stages` 未同步 `task.reviewer_id`，`reassign_stage(0)` 未同步 `task.assignee_id`

- **状态**：已确认方案（待修改）
- **确认口径**：
  1. `append_stages` 事务内同步 `task.reviewer_id = _compat_reviewer_for(new_stages, cursor)`；
  2. `reassign_stage` 更换节点 0（`EXECUTION`）负责人时，同步更新主表 `task.assignee_id`。
- **涉及文件**：
  - `src/service/taskService.py`：`_apply_stage_update`（L1215-L1225、L1317-L1325）
- **问题描述**：
  1. **`append_stages`**：`remove_stage_index` 和 `reassign_stage` 都会调用 `_compat_reviewer_for(...)` 同步 `task.reviewer_id`，但 `append_stages` 漏掉了。若任务创建时无 `reviewer_id`（`reviewer_id=None`），认领后通过 `append_stages` 后置加签了一个 `REVIEW` 节点，此时若执行人按 v2 习惯调用 `new_status="REVIEWING"`，会被 `_update_task_impl` L1657（`task.reviewer_id is None`）误拒为 `no_reviewer`。
  2. **`reassign_stage`（针对节点 0）**：更换节点 0（`EXECUTION`）负责人时，更新了 `stage.owner_id` 和 `task.current_owner_id`，但未同步 `task.assignee_id`。在未认领（`TODO`）状态下更换节点 0 后，新负责人调用 `claim_task`（L846 校验 `task.assignee_id != caller_id`）会失效，且按 `assignee` 过滤的查询仍归属旧人。
- **建议方案**：
  - `append_stages` 事务内同步更新 `task.reviewer_id = _compat_reviewer_for(new_stages, cursor)`。
  - `reassign_stage` 当 `raw_index == 0` 时同步更新 `task.assignee_id = raw_owner`（或明确节点 0 换人与 `assignee_id` 的唯一真值口径）。

---

### [S-5] P1：`get_first_active_task` 在多级 `EXECUTION` 节点下会误唤醒非当前持棒人

- **状态**：待讨论
- **涉及文件**：
  - `src/dal/db/gtAgentTaskManager.py`（L230-L258）
- **问题描述**：
  - 当前查询条件把 v2 的 `(assignee_id == agent_id & status IN [TODO, IN_PROGRESS]) | (reviewer_id == agent_id & status == REVIEWING)` 与 `current_owner_id == agent_id` 直接做了 `OR`。
  - 在多级 `EXECUTION` 链路中（例如 `[EXEC(A), EXEC(B), REVIEW(C)]` 棒已传到 `B`，此时 `status=IN_PROGRESS, current_owner_id=B, assignee_id=A`），`assignee_id == A & status == IN_PROGRESS` 的 v2 分支依然会命中，`A` 会拿到一个「幽灵待办」触发调度。
  - （注：多级 `REVIEW` 链下因 `_compat_reviewer_for` 在停于 `REVIEW` 节点时会同步 `reviewer_id = 当前节点 owner`，故 `REVIEWING` 分支不会提前命中后续审核人。）
- **建议方案**：
  - 对于 `IN_PROGRESS` 和 `REVIEWING`，应以 `current_owner_id == agent_id` 为准（仅在 `current_owner_id IS NULL` 时退化兼容 `assignee_id` / `reviewer_id`）；`TODO` 仍按 `assignee_id == agent_id`（或 `current_owner_id == agent_id`）。

---

### [S-6] P2：末节点为 `EXECUTION` 且前序含 `REVIEW` 时，v2 兼容写法 `new_status="DONE"` 会被误拒

- **状态**：待讨论
- **涉及文件**：
  - `src/service/taskService.py`：`_update_task_impl`（L1678-L1687）
- **问题描述**：
  - 对于 `[EXEC(A), REVIEW(B), EXEC(C)]` 这类链路，当棒走到节点 2（`EXEC(C)`）时，`status=IN_PROGRESS`，而 `task.reviewer_id`（最后一个 `REVIEW` 节点）仍为 `B`。
  - 此时 `C` 已处于**最后一个节点**，按工具文档提示调用 `new_status="DONE"`，会被 L1678-L1687（`target == DONE and old_status != REVIEWING and task.reviewer_id is not None`）误拦截并返回 `review_required`。
- **建议方案**：
  - 在 L1678 的 `review_required` 检查中，判断当前游标后面是否还有未通过的 `REVIEW` 节点（或仅在 `cursor < last_index` 时拦截），与 `_plan_for_compat_transition` 的末节点判定对齐。
