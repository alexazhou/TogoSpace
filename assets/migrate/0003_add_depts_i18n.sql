-- Add i18n column to depts table
-- This migration adds the i18n field to depts table for multi-language department names and responsibilities

ALTER TABLE depts ADD COLUMN i18n TEXT NOT NULL DEFAULT '{}';