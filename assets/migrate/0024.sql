-- Rename agent_histories.message_json to message
ALTER TABLE agent_histories RENAME COLUMN message_json TO message;
