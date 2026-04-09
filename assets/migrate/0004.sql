-- 修改 agents 表的 (team_id, name) 索引为非唯一索引，允许离职成员名字被复用

DROP INDEX IF EXISTS agents_team_id_name;
CREATE INDEX agents_team_id_name ON agents (team_id, name);