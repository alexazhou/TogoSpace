ALTER TABLE room_messages ADD COLUMN seq INTEGER;

UPDATE room_messages SET seq = (
    SELECT COUNT(*) - 1
    FROM room_messages AS m2
    WHERE m2.room_id = room_messages.room_id
    AND m2.id <= room_messages.id
);
