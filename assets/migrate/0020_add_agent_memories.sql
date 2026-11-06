-- 0020: Agent 长期记忆系统 —— 新增记忆表 agent_memories。
--
-- 设计要点（docs/design/2026-11-06-agent-memory-system/step2_technical.md §2.1）：
-- ① 文本列统一 TEXT，不施加 DB 层长度约束：标题 / 概要 / 正文的长度上限、同名防重、
--    星标配额等规则全部在 memoryService 层集中校验，后续调整配额无需迁移表结构。
-- ② star_level / status 用 EnumField 口径存枚举 **name**（NONE/TITLE/SUMMARY、ACTIVE/ARCHIVED），
--    与全仓其他 EnumField 一致（见 src/model/dbModel/base.py 的 db_value）。
-- ③ 记忆严格绑定 (team_id, agent_id)，单 Agent 私有，本期不做跨 Agent 公共记忆。
CREATE TABLE IF NOT EXISTS agent_memories (
    id          INTEGER  PRIMARY KEY AUTOINCREMENT,
    team_id     INTEGER  NOT NULL,                   -- 所属团队 ID
    agent_id    INTEGER  NOT NULL,                   -- 所属 Agent ID
    title       TEXT     NOT NULL,                   -- 唯一主题标题（单行，≤ 80 字符，Service 层校验）
    summary     TEXT     NOT NULL,                   -- 单句结论概要（单行，≤ 120 字符，Service 层校验）
    content     TEXT     NOT NULL,                   -- 可执行完整正文（≤ 1000 字符，Service 层校验）
    star_level  TEXT     NOT NULL DEFAULT 'NONE',    -- 星标级别：NONE / TITLE / SUMMARY
    status      TEXT     NOT NULL DEFAULT 'ACTIVE',  -- 状态：ACTIVE / ARCHIVED
    tags        TEXT     NOT NULL DEFAULT '[]',      -- 标签 JSON 数组字符串（≤ 5 个，单个 ≤ 24 字符）
    created_at  DATETIME NOT NULL DEFAULT (datetime('now')),
    updated_at  DATETIME NOT NULL DEFAULT (datetime('now'))
);

-- 日常列表 / 检索 / 同名防重查询（都带 team_id + agent_id + status）
CREATE INDEX IF NOT EXISTS idx_agent_memories_agent_status ON agent_memories (team_id, agent_id, status);

-- 星标快照装配与配额统计（都带 team_id + agent_id + star_level）
CREATE INDEX IF NOT EXISTS idx_agent_memories_agent_star ON agent_memories (team_id, agent_id, star_level);
