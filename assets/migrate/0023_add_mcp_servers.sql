-- #273（Operator 2026-11-15 17:54 需求变更）：**全局** MCP server 注册表。
--
-- 编号必须是 0023：主干 assets/migrate/0022 已是 0022_add_agent_task_stages.sql
-- （任务系统 v3，#252 合入）且已在各环境执行过（迁移账本 id=22，本机 2026-11-15 05:16），
-- 0022 一个字都不能动。原 #224 半成品的 0022_add_mcp_servers.sql 从未在任何环境
-- 执行过（该分支未合并），mcp_servers 表不存在存量 ⇒ 无兼容负担，按新结构一次写对。
--
-- 与 #224 半成品的结构差异：
--   ① 去 team_id：注册表全局共享，唯一约束改为全局唯一的 name；
--   ② alias → name（功能从未发布，不留兼容别名）；
--   ③ 新增 enable INTEGER NOT NULL DEFAULT 1（禁用 ≠ 删除：工具列表里消失、
--      mcp call 明确报「已禁用」，mcp ls 仍列出并标注）。
CREATE TABLE IF NOT EXISTS mcp_servers (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  name            TEXT    NOT NULL,
  kind            TEXT    NOT NULL DEFAULT 'mcp',
  target          TEXT    NOT NULL,
  note            TEXT    NOT NULL DEFAULT '',
  credentials     TEXT    NOT NULL DEFAULT '{}',
  enable          INTEGER NOT NULL DEFAULT 1,
  created_by      INTEGER NOT NULL DEFAULT 0,
  created_at      DATETIME NOT NULL DEFAULT (datetime('now', 'localtime')),
  updated_at      DATETIME NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_mcp_servers_name ON mcp_servers(name);
