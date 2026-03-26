ALTER TABLE member_histories ADD COLUMN member_id INTEGER NOT NULL DEFAULT 0;
UPDATE member_histories
SET member_id = (
    SELECT tm.id FROM team_members tm
    WHERE tm.team_id = member_histories.team_id
      AND tm.name = member_histories.member_name
    LIMIT 1
);
DROP INDEX IF EXISTS member_histories_team_member_seq;
CREATE UNIQUE INDEX IF NOT EXISTS member_histories_member_seq
ON member_histories(member_id, seq);
ALTER TABLE member_histories DROP COLUMN team_id;
ALTER TABLE member_histories DROP COLUMN member_name;

