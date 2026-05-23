PRAGMA foreign_keys=off;

BEGIN TRANSACTION;

ALTER TABLE rooms RENAME TO rooms_old_0011;

CREATE TABLE rooms (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id          INTEGER NOT NULL,
    name             TEXT    NOT NULL,
    type             TEXT    NOT NULL,
    biz_id           TEXT,
    initial_topic    TEXT,
    max_turns        INTEGER,
    turn_pos         INTEGER DEFAULT NULL,
    agent_read_index TEXT,
    agent_ids        TEXT    DEFAULT '[]',
    tags             TEXT    NOT NULL DEFAULT '[]',
    i18n             TEXT    NOT NULL DEFAULT '{}',
    created_at       TEXT    NOT NULL DEFAULT '',
    updated_at       TEXT    NOT NULL
);

INSERT INTO rooms (
    id,
    team_id,
    name,
    type,
    biz_id,
    initial_topic,
    max_turns,
    turn_pos,
    agent_read_index,
    agent_ids,
    tags,
    i18n,
    created_at,
    updated_at
)
SELECT
    id,
    team_id,
    name,
    type,
    biz_id,
    initial_topic,
    CASE
        WHEN max_turns = 100 THEN NULL
        ELSE max_turns
    END,
    turn_pos,
    agent_read_index,
    agent_ids,
    tags,
    COALESCE(i18n, '{}'),
    created_at,
    updated_at
FROM rooms_old_0011;

DROP TABLE rooms_old_0011;

CREATE UNIQUE INDEX rooms_team_id_name ON rooms(team_id, name);

COMMIT;

PRAGMA foreign_keys=on;
