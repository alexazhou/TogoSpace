-- 添加 employee_number 字段（Agent 在 Team 内的工号）
ALTER TABLE agents ADD COLUMN employee_number INTEGER NOT NULL DEFAULT 0;

-- 为已有数据分配工号：每个 team 内按 id 顺序递增
-- 使用 SQLite 的 ROW_NUMBER（需要 SQLite 3.25+，但为了兼容性使用传统方式）

-- 创建临时表存储 team_id 和其 agents 的排序
CREATE TEMP TABLE _tmp_agent_order AS
SELECT id, team_id, ROW_NUMBER() OVER (PARTITION BY team_id ORDER BY id) AS new_num
FROM agents WHERE employee_number = 0;

-- 更新已有 agents 的工号
UPDATE agents
SET employee_number = (
    SELECT new_num FROM _tmp_agent_order WHERE _tmp_agent_order.id = agents.id
)
WHERE employee_number = 0;

-- 删除临时表
DROP TABLE _tmp_agent_order;

-- 创建 Team 内唯一索引
CREATE UNIQUE INDEX IF NOT EXISTS agents_team_id_employee_number
ON agents(team_id, employee_number);