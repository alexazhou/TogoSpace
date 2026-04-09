-- Create agent_activities table
CREATE TABLE IF NOT EXISTS agent_activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id INTEGER NOT NULL,
    team_id INTEGER NOT NULL,
    activity_type TEXT NOT NULL,
    status TEXT NOT NULL,
    title TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    error_message TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    duration_ms INTEGER,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_agent_activities_team_id ON agent_activities(team_id, id);
CREATE INDEX IF NOT EXISTS idx_agent_activities_agent_id ON agent_activities(agent_id, id);
