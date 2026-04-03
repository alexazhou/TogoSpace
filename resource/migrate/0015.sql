-- Add stage/success/error_message to agent_histories

ALTER TABLE agent_histories
ADD COLUMN stage TEXT NOT NULL DEFAULT 'INPUT';

ALTER TABLE agent_histories
ADD COLUMN success INTEGER;

ALTER TABLE agent_histories
ADD COLUMN error_message TEXT;
