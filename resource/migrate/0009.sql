-- Add role template runtime fields: driver and allowed_tools

ALTER TABLE role_templates ADD COLUMN driver TEXT NULL;
ALTER TABLE role_templates ADD COLUMN allowed_tools TEXT NULL;
