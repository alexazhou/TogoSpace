-- Add role template type to distinguish built-in and user-created templates

ALTER TABLE role_templates ADD COLUMN type TEXT NOT NULL DEFAULT 'SYSTEM';
