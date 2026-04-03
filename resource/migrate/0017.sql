-- Remove obsolete team-level max_function_calls column

ALTER TABLE teams
DROP COLUMN max_function_calls;
