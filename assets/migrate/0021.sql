-- 将 agent_histories.usage_json 重命名为 usage
ALTER TABLE agent_histories RENAME COLUMN usage_json TO usage;
