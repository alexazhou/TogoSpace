-- Add tags to agent_histories

ALTER TABLE agent_histories
ADD COLUMN tags TEXT NOT NULL DEFAULT '[]';
