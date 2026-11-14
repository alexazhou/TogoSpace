-- #224（按 #223 设计稿 §2.3）：team 级 MCP server 注册表。
-- 注意：本迁移与 #227（任务系统 v3，李哥线）的 0022 同号——两并行分支同号，
-- 合并时按「后合者重编号」处理（PM 2026-11-14 13:15 拍板，#224 事件 1343）。
CREATE TABLE IF NOT EXISTS mcp_servers (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  team_id         INTEGER NOT NULL,
  alias           TEXT    NOT NULL,
  kind            TEXT    NOT NULL DEFAULT 'mcp',
  target          TEXT    NOT NULL,
  note            TEXT    NOT NULL DEFAULT '',
  credentials     TEXT    NOT NULL DEFAULT '{}',
  created_by      INTEGER NOT NULL DEFAULT 0,
  created_at      DATETIME NOT NULL,
  updated_at      DATETIME NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_mcp_servers_team_alias ON mcp_servers(team_id, alias);
