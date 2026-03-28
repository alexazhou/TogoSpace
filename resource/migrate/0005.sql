-- Add deleted field to teams table
ALTER TABLE teams ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0;