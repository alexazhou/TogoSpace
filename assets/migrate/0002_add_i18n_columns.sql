-- Add i18n columns to existing tables
-- This migration adds the i18n field to tables that may have been created before this field was introduced

-- Add i18n to teams table
ALTER TABLE teams ADD COLUMN i18n TEXT NOT NULL DEFAULT '{}';

-- Add i18n to agents table
ALTER TABLE agents ADD COLUMN i18n TEXT NOT NULL DEFAULT '{}';

-- Add i18n to rooms table
ALTER TABLE rooms ADD COLUMN i18n TEXT NOT NULL DEFAULT '{}';

-- Add i18n to role_templates table
ALTER TABLE role_templates ADD COLUMN i18n TEXT NOT NULL DEFAULT '{}';