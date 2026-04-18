-- ToGo Agent Initial Database Schema
-- Consolidated from migrations 0001 to 0026

-- 1. Teams Table
CREATE TABLE IF NOT EXISTS teams (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL UNIQUE,
    uuid         TEXT    DEFAULT NULL,
    enabled      BOOLEAN NOT NULL DEFAULT 1,
    deleted      INTEGER NOT NULL DEFAULT 0,
    config       TEXT    NOT NULL DEFAULT '{}',
    i18n         TEXT    NOT NULL DEFAULT '{}',
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_teams_uuid ON teams(uuid) WHERE uuid IS NOT NULL;

-- 2. Agents Table
CREATE TABLE IF NOT EXISTS agents (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id          INTEGER NOT NULL,
    employee_number  INTEGER NOT NULL DEFAULT 0,
    name             TEXT    NOT NULL,
    role_template_id INTEGER NOT NULL DEFAULT 0,
    employ_status    TEXT    NOT NULL DEFAULT 'ON_BOARD',
    model            TEXT    NOT NULL DEFAULT '',
    driver           TEXT    NOT NULL DEFAULT 'NATIVE',
    i18n             TEXT    NOT NULL DEFAULT '{}',
    created_at       TEXT    NOT NULL DEFAULT '',
    updated_at       TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS agents_team_id_name ON agents (team_id, name);
CREATE UNIQUE INDEX IF NOT EXISTS agents_team_id_employee_number ON agents(team_id, employee_number);

-- 3. Rooms Table
CREATE TABLE IF NOT EXISTS rooms (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id          INTEGER NOT NULL,
    name             TEXT    NOT NULL,
    type             TEXT    NOT NULL,
    biz_id           TEXT,
    initial_topic    TEXT,
    max_turns        INTEGER NOT NULL DEFAULT 100,
    turn_pos         INTEGER NOT NULL DEFAULT 0,
    agent_read_index TEXT,
    agent_ids        TEXT    DEFAULT '[]',
    tags             TEXT    NOT NULL DEFAULT '[]',
    i18n             TEXT    NOT NULL DEFAULT '{}',
    created_at       TEXT    NOT NULL DEFAULT '',
    updated_at       TEXT    NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS rooms_team_id_name ON rooms(team_id, name);

-- 4. Room Messages Table
CREATE TABLE IF NOT EXISTS room_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id    INTEGER NOT NULL,
    agent_id   INTEGER NOT NULL DEFAULT 0,
    content    TEXT    NOT NULL,
    send_time  TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT '',
    updated_at TEXT    NOT NULL DEFAULT ''
);

-- 5. Agent Histories Table (Consolidated to latest structure)
CREATE TABLE IF NOT EXISTS agent_histories (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id      INTEGER NOT NULL DEFAULT 0,
    seq           INTEGER NOT NULL,
    role          TEXT    NOT NULL,
    tool_call_id  TEXT,
    message       TEXT,
    status        TEXT    NOT NULL DEFAULT 'INIT',
    error_message TEXT,
    tags          TEXT    NOT NULL DEFAULT '[]',
    usage         TEXT,
    created_at    TEXT    NOT NULL DEFAULT '',
    updated_at    TEXT    NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS agent_histories_agent_seq ON agent_histories(agent_id, seq);

-- 6. Role Templates Table
CREATE TABLE IF NOT EXISTS role_templates (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL UNIQUE,
    model         TEXT,
    soul          TEXT    NOT NULL DEFAULT '',
    allowed_tools TEXT,
    type          TEXT    NOT NULL DEFAULT 'SYSTEM',
    i18n          TEXT    NOT NULL DEFAULT '{}',
    created_at    TEXT    NOT NULL DEFAULT '',
    updated_at    TEXT    NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS role_templates_name ON role_templates(name);

-- 7. Departments Table
CREATE TABLE IF NOT EXISTS depts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id        INTEGER NOT NULL REFERENCES teams(id),
    name           TEXT    NOT NULL,
    responsibility TEXT    NOT NULL DEFAULT '',
    parent_id      INTEGER REFERENCES depts(id),
    manager_id     INTEGER NOT NULL,
    agent_ids      TEXT    NOT NULL DEFAULT '[]',
    created_at     TEXT    NOT NULL,
    updated_at     TEXT    NOT NULL,
    UNIQUE (team_id, name)
);

-- 8. System Configs Table
CREATE TABLE IF NOT EXISTS system_configs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    key        TEXT    NOT NULL UNIQUE,
    value      TEXT    NOT NULL DEFAULT '',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 9. Agent Tasks Table
CREATE TABLE IF NOT EXISTS agent_tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id      INTEGER NOT NULL,
    task_type     TEXT    NOT NULL DEFAULT 'ROOM_MESSAGE',
    task_data     TEXT    NOT NULL DEFAULT '{}',
    status        TEXT    NOT NULL DEFAULT 'PENDING',
    error_message TEXT    NULL,
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_agent_tasks_agent_status ON agent_tasks(agent_id, status);

-- 10. Agent Activities Table
CREATE TABLE IF NOT EXISTS agent_activities (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id      INTEGER NOT NULL,
    team_id       INTEGER NOT NULL,
    activity_type TEXT    NOT NULL,
    status        TEXT    NOT NULL,
    title         TEXT    NOT NULL,
    detail        TEXT    NOT NULL DEFAULT '',
    error_message TEXT,
    started_at    TEXT    NOT NULL,
    finished_at   TEXT,
    duration_ms   INTEGER,
    metadata      TEXT    NOT NULL DEFAULT '{}',
    created_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_agent_activities_team_id ON agent_activities(team_id, id);
CREATE INDEX IF NOT EXISTS idx_agent_activities_agent_id ON agent_activities(agent_id, id);
