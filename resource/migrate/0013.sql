-- Rename role_templates.template_name to role_templates.name

DROP INDEX IF EXISTS role_templates_template_name;
ALTER TABLE role_templates RENAME COLUMN template_name TO name;
CREATE UNIQUE INDEX IF NOT EXISTS role_templates_name
ON role_templates(name);
