-- Add role template allowed_tools field

ALTER TABLE role_templates ADD COLUMN allowed_tools TEXT NULL;
