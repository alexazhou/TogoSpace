ALTER TABLE room_messages ADD COLUMN sender_id INTEGER;
UPDATE room_messages SET sender_id = agent_id;
ALTER TABLE room_messages DROP COLUMN agent_id;
