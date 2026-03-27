-- V10: 为所有表补充 created_at 字段；为 room_messages 补充 created_at + updated_at

ALTER TABLE rooms           ADD COLUMN created_at TEXT NOT NULL DEFAULT "";
ALTER TABLE team_members    ADD COLUMN created_at TEXT NOT NULL DEFAULT "";
ALTER TABLE agents          ADD COLUMN created_at TEXT NOT NULL DEFAULT "";
ALTER TABLE member_histories ADD COLUMN created_at TEXT NOT NULL DEFAULT "";
ALTER TABLE room_messages   ADD COLUMN created_at TEXT NOT NULL DEFAULT "";
ALTER TABLE room_messages   ADD COLUMN updated_at TEXT NOT NULL DEFAULT "";
