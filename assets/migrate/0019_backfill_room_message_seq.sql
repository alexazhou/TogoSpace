-- 0019: 为 room_messages 补齐存量 seq（seq 从「可空 + 未注入=NULL」改为「插入即分配」）。
--
-- 背景：旧实现把「显示顺序」与「是否待注入」两个职责压在同一个 seq 列上，
-- 未注入消息 seq=NULL、注入时才赋值。新方案让 seq 插入即分配，
-- 因此存量里遗留的 seq IS NULL 必须补齐，否则排序与读游标（next_seq）都会错位。
--
-- 三条关键设计：
-- ① **用 COALESCE(MAX(seq), -1) 起号，不用 COUNT**。
--    旧版「升级为立即注入」会把消息 seq 置回 NULL，于是历史上会留下 seq 空洞
--    （例如 0,1,3 而 2 被清空）。用 COUNT 起号会撞上已存在的 3。
--    MAX(seq)+1 保证新号严格在现有最大值之后。
--    `COALESCE(..., -1)` 让「房间内一条非空 seq 都没有」时从 0 开始。
-- ② **同房间内按 (insert_immediately DESC, id ASC) 定序**。
--    旧语义下 insert_immediately 的消息是「插到前面」的，补齐时保持这个直觉；
--    同组内按 id 升序 = 发送顺序。
-- ③ **seq 列保留 nullable，不重建表收紧 NOT NULL**。
--    业务层恒有值即可；SQLite 收紧 NOT NULL 需重建表并搬数据，
--    在大表上有失败风险且不可回滚，收益仅是与代码约定对齐。不做。
--
-- 事务：`db.py:apply_migration` 用 `conn.executescript(sql)` 执行本文件，而
-- `executescript` 进入时会先**隐式 COMMIT**，`with conn:` 的异常回滚兜不住。
-- 本迁移是纯 UPDATE（DDL 无关），但中途失败同样会把库停在「部分房间已补齐、
-- 部分未补齐」的半状态；且部分工具链下 UPDATE 也会被切断。
-- 故仍显式 BEGIN/COMMIT，保证整体回滚。参照 0018 的同一约定。
--
-- 幂等：WHERE seq IS NULL 天然幂等 —— 重复执行时没有待补行，不影响任何数据。

BEGIN;

WITH base AS (
    SELECT room_id, COALESCE(MAX(seq), -1) AS max_seq
    FROM room_messages
    GROUP BY room_id
),
ranked AS (
    SELECT
        m.id,
        b.max_seq + ROW_NUMBER() OVER (
            PARTITION BY m.room_id
            ORDER BY m.insert_immediately DESC, m.id ASC
        ) AS new_seq
    FROM room_messages AS m
    JOIN base AS b ON b.room_id = m.room_id
    WHERE m.seq IS NULL
)
UPDATE room_messages
SET seq = (SELECT new_seq FROM ranked WHERE ranked.id = room_messages.id)
WHERE seq IS NULL;

COMMIT;
