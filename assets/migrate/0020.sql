-- agent_histories 增加 usage_json 字段：记录 LLM 推理的 token 用量
ALTER TABLE agent_histories ADD COLUMN usage_json TEXT;
