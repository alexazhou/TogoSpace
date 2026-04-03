-- Replace success flag with status enum in agent_histories

ALTER TABLE agent_histories
ADD COLUMN status TEXT NOT NULL DEFAULT 'INIT';

UPDATE agent_histories
SET status = CASE
    WHEN success = 0 THEN 'FAILED'
    ELSE 'SUCCESS'
END;
