-- Rebuild agent_histories again to remove SQL-level CHECK constraints

CREATE TABLE agent_histories_new (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id     INTEGER NOT NULL DEFAULT 0,
    seq          INTEGER NOT NULL,
    role         TEXT    NOT NULL,
    tool_call_id TEXT,
    message_json TEXT,
    status       TEXT    NOT NULL DEFAULT 'INIT',
    error_message TEXT,
    tags         TEXT    NOT NULL DEFAULT '[]',
    usage        TEXT,
    created_at   TEXT    NOT NULL DEFAULT '',
    updated_at   TEXT    NOT NULL
);

INSERT INTO agent_histories_new (
    id,
    agent_id,
    seq,
    role,
    tool_call_id,
    message_json,
    status,
    error_message,
    tags,
    usage,
    created_at,
    updated_at
)
SELECT
    id,
    agent_id,
    seq,
    role,
    tool_call_id,
    message_json,
    status,
    error_message,
    tags,
    usage,
    created_at,
    updated_at
FROM agent_histories;

DROP TABLE agent_histories;
ALTER TABLE agent_histories_new RENAME TO agent_histories;

CREATE UNIQUE INDEX IF NOT EXISTS agent_histories_agent_seq
ON agent_histories(agent_id, seq);
