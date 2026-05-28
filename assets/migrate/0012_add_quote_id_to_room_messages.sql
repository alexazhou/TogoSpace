ALTER TABLE room_messages ADD COLUMN quote_id INTEGER;
CREATE INDEX IF NOT EXISTS ix_room_messages_quote_id ON room_messages (quote_id);
