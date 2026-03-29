-- Add soul column to role_templates table for storing system_prompt
-- Also modify model column to allow null (SQLite requires table rebuild)

-- Step 1: Add soul column (this is supported)
ALTER TABLE role_templates ADD COLUMN soul TEXT NOT NULL DEFAULT '';

-- Step 2: Rebuild table to make model nullable
CREATE TABLE role_templates_new (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    template_name TEXT NOT NULL UNIQUE,
    model         TEXT,  -- Now nullable
    soul          TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL DEFAULT '',
    updated_at    TEXT NOT NULL
);

-- Step 3: Copy data
INSERT INTO role_templates_new (id, template_name, model, soul, created_at, updated_at)
SELECT id, template_name, NULLIF(model, ''), soul, created_at, updated_at
FROM role_templates;

-- Step 4: Drop old table and rename
DROP TABLE role_templates;
ALTER TABLE role_templates_new RENAME TO role_templates;

-- Step 5: Recreate index
CREATE UNIQUE INDEX IF NOT EXISTS role_templates_template_name
ON role_templates(template_name);