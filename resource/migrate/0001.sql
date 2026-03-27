CREATE TABLE IF NOT EXISTS teams (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    name               TEXT    NOT NULL UNIQUE,
    max_function_calls INTEGER NOT NULL DEFAULT 5,
    enabled            INTEGER NOT NULL DEFAULT 1,
    working_directory  TEXT    NOT NULL DEFAULT '',
    config             TEXT    NOT NULL DEFAULT '{}',
    created_at         TEXT    NOT NULL,
    updated_at         TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS team_members (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id       INTEGER NOT NULL,
    name          TEXT    NOT NULL,
    agent_name    TEXT    NOT NULL,
    employ_status TEXT    NOT NULL DEFAULT 'ON_BOARD',
    model         TEXT    NOT NULL DEFAULT '',
    driver        TEXT    NOT NULL DEFAULT '{}',
    created_at    TEXT    NOT NULL DEFAULT '',
    updated_at    TEXT    NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS team_members_team_id_name
ON team_members(team_id, name);

CREATE TABLE IF NOT EXISTS rooms (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id           INTEGER NOT NULL,
    name              TEXT    NOT NULL,
    type              TEXT    NOT NULL,
    initial_topic     TEXT,
    max_turns         INTEGER NOT NULL DEFAULT 100,
    member_read_index TEXT,
    member_ids        TEXT    DEFAULT '[]',
    created_at        TEXT    NOT NULL DEFAULT '',
    updated_at        TEXT    NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS rooms_team_id_name
ON rooms(team_id, name);

CREATE TABLE IF NOT EXISTS room_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id    INTEGER NOT NULL,
    member_id  INTEGER NOT NULL DEFAULT 0,
    content    TEXT    NOT NULL,
    send_time  TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT '',
    updated_at TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS member_histories (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id    INTEGER NOT NULL DEFAULT 0,
    seq          INTEGER NOT NULL,
    message_json TEXT    NOT NULL,
    created_at   TEXT    NOT NULL DEFAULT '',
    updated_at   TEXT    NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS member_histories_member_seq
ON member_histories(member_id, seq);

CREATE TABLE IF NOT EXISTS agents (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    template_name TEXT NOT NULL,
    model         TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL DEFAULT '',
    updated_at    TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS agents_template_name
ON agents(template_name);

CREATE TABLE IF NOT EXISTS depts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id        INTEGER NOT NULL REFERENCES teams(id),
    name           TEXT    NOT NULL,
    responsibility TEXT    NOT NULL DEFAULT '',
    parent_id      INTEGER REFERENCES depts(id),
    manager_id     INTEGER NOT NULL,
    member_ids     TEXT    NOT NULL DEFAULT '[]',
    created_at     TEXT    NOT NULL,
    updated_at     TEXT    NOT NULL,
    UNIQUE (team_id, name)
);
