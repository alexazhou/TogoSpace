-- 0017: Docker 容器化 Agent 沙箱 —— 新增自建容器记录表 agent_containers。
--
-- 只记录 create_container 创建的容器；外部/他人容器不入库（不进配额、不出现在列表）。
-- 运行状态实时 docker inspect，不落库，避免缓存漂移。
-- 当前进入状态只在内存（Driver），重启一律回宿主，不落库。
CREATE TABLE IF NOT EXISTS agent_containers (
    id             INTEGER  PRIMARY KEY AUTOINCREMENT,
    agent_id       INTEGER  NOT NULL,                 -- 所属 Agent ID
    container_id   TEXT,                              -- Docker 容器 12 位短 ID；创建流程先插入 NULL，create 成功后回填
    container_name TEXT     NOT NULL,                 -- agent-t<team_id>-<name>-<record_id>（record_id = 本表 id）
    image          TEXT     NOT NULL,                 -- 创建时实际使用的镜像
    deleted        INTEGER  NOT NULL DEFAULT 0,       -- 软删除标记；1=已删除
    created_at     DATETIME NOT NULL DEFAULT (datetime('now')),
    updated_at     DATETIME NOT NULL DEFAULT (datetime('now'))
);

-- 按 Agent 查活跃容器（配额统计 / list_containers）
CREATE INDEX IF NOT EXISTS idx_agent_containers_agent ON agent_containers (agent_id, deleted);

-- container_id 全局唯一（NULL 不参与唯一约束，兼容创建中的预记录）
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_containers_container_id ON agent_containers (container_id);

-- container_name 不建唯一索引：名称末尾的 record_id（自增主键）已保证不重复。
