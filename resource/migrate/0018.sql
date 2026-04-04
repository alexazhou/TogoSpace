-- Agent Tasks 表：存储 Agent 任务记录，用于任务持久化与恢复
CREATE TABLE IF NOT EXISTS agent_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id INTEGER NOT NULL,
    task_type TEXT NOT NULL DEFAULT 'ROOM_MESSAGE',
    task_data TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'PENDING',
    error_message TEXT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 索引：按 agent_id 和 status 查询待处理任务
CREATE INDEX IF NOT EXISTS idx_agent_tasks_agent_status ON agent_tasks(agent_id, status);