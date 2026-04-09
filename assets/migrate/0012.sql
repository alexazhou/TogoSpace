-- Migration 0012: Add biz_id and tags columns to rooms table
ALTER TABLE rooms ADD COLUMN biz_id TEXT;
ALTER TABLE rooms ADD COLUMN tags TEXT NOT NULL DEFAULT '[]';