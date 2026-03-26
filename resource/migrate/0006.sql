ALTER TABLE agent_histories RENAME TO member_histories;
ALTER TABLE member_histories RENAME COLUMN agent_name TO member_name;
DROP INDEX IF EXISTS agent_histories_team_agent_seq;
CREATE UNIQUE INDEX IF NOT EXISTS member_histories_team_member_seq
ON member_histories(team_id, member_name, seq);
