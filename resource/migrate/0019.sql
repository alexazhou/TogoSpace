-- 房间增加 turn_pos 字段：持久化当前发言位索引，重启后可恢复调度状态
ALTER TABLE rooms ADD COLUMN turn_pos INTEGER NOT NULL DEFAULT 0;
