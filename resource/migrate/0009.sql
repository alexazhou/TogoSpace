-- Deduplicate: keep one agent record per template_name (smallest id wins)
DELETE FROM agents WHERE id NOT IN (
    SELECT MIN(id) FROM agents GROUP BY template_name
);

DROP INDEX IF EXISTS agents_team_id_name;
CREATE UNIQUE INDEX IF NOT EXISTS agents_template_name ON agents(template_name);

ALTER TABLE agents DROP COLUMN team_id;
ALTER TABLE agents DROP COLUMN name;
