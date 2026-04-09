-- Replace agent role template reference with role_template_id.
-- Development phase only: adjust table structure without migrating old data.

ALTER TABLE agents ADD COLUMN role_template_id INTEGER NOT NULL DEFAULT 0;
ALTER TABLE agents DROP COLUMN role_template_name;
