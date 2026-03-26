ALTER TABLE room_members ADD COLUMN member_id INTEGER NOT NULL DEFAULT 0;
UPDATE room_members
SET member_id = (
    SELECT tm.id FROM team_members tm
    JOIN rooms r ON r.id = room_members.room_id
    WHERE tm.team_id = r.team_id AND tm.name = room_members.agent_name
    LIMIT 1
);
ALTER TABLE room_members DROP COLUMN agent_name;

ALTER TABLE room_messages ADD COLUMN member_id INTEGER NOT NULL DEFAULT 0;
UPDATE room_messages
SET member_id = (
    SELECT tm.id FROM team_members tm
    JOIN rooms r ON r.id = room_messages.room_id
    WHERE tm.team_id = r.team_id AND tm.name = room_messages.agent_name
    LIMIT 1
);
ALTER TABLE room_messages DROP COLUMN agent_name;

ALTER TABLE rooms RENAME COLUMN agent_read_index TO member_read_index;
