-- Add member_ids JSON column to rooms table
ALTER TABLE rooms ADD COLUMN member_ids TEXT DEFAULT '[]';

-- Drop room_members table
DROP TABLE IF EXISTS room_members;