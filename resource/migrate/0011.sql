-- V10: 新增 depts 表 + 扩展 team_members 表

CREATE TABLE IF NOT EXISTS depts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id        INTEGER NOT NULL REFERENCES teams(id),
    name           TEXT    NOT NULL,
    responsibility TEXT    NOT NULL DEFAULT "",
    parent_id      INTEGER REFERENCES depts(id),
    manager_id     INTEGER NOT NULL,
    member_ids     TEXT    NOT NULL DEFAULT "[]",
    created_at     TEXT    NOT NULL,
    updated_at     TEXT    NOT NULL,
    UNIQUE (team_id, name)
);

ALTER TABLE team_members ADD COLUMN employ_status TEXT NOT NULL DEFAULT "ON_BOARD";
ALTER TABLE team_members ADD COLUMN model         TEXT NOT NULL DEFAULT "";
ALTER TABLE team_members ADD COLUMN driver        TEXT NOT NULL DEFAULT "{}";
