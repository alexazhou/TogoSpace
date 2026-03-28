-- 将 driver 字段从 JSON 格式转换为枚举名称
-- 原格式: '{"type": "native"}' -> 新格式: 'NATIVE'

UPDATE agents SET driver = 'NATIVE' WHERE driver LIKE '%"type": "native"%';
UPDATE agents SET driver = 'NATIVE' WHERE driver = '{}' OR driver = '' OR driver IS NULL;
UPDATE agents SET driver = 'CLAUDE_SDK' WHERE driver LIKE '%"type": "claude_sdk"%';
UPDATE agents SET driver = 'TSP' WHERE driver LIKE '%"type": "tsp"%';