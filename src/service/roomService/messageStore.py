from __future__ import annotations

import logging
from typing import Dict, List

from dal.db import gtAgentManager, gtRoomMessageManager

logger = logging.getLogger("service.roomService")
from constants import SpecialAgent
from model.dbModel.gtRoom import GtRoom
from model.dbModel.gtRoomMessage import GtRoomMessage
from service import messageBus
from constants import MessageBusTopic


class RoomMessageStore:
    """管理房间内存消息列表、各 Agent 已读进度、DB 持久化与 WS 事件广播。

    三个职责彼此正交，不得互相耦合（这是本类唯一的不变量）：

    1. **顺序（`seq`）**：插入即分配，严格按写入顺序单调递增（0, 1, 2, ...）。
       不存在「seq=NULL 表示未注入」的中间态。
    2. **未读（`_agent_seq_read`）**：每个 Agent 一条读游标，指向「下一条待读的 seq」。
       「是否未读」完全由游标与 seq 比较得出，与 insert_immediately 无关。
    3. **立即注入触发（`insert_immediately`）**：**只**决定「安全边界要不要提前收消息」，
       不参与 seq 分配、不影响可见性、不改变顺序。

    旧实现把 1 和 3 压在同一个 seq 列上（seq=NULL 兼作「待注入」标记），由此派生出
    pending 队列、flush_pending_immediate、flush_queued、_sort 一整套状态机。
    本类**不再提供**这些入口。
    """

    # 内存窗口大小：内存里最多保留最近这么多条消息，更早的只在 DB。
    # 固定窗口（而非按最小读游标动态收缩）——淘汰是机械截断，无需维护动态下界。
    WINDOW_SIZE = 100

    def __init__(self, *, gt_room: GtRoom):
        self._messages: List[GtRoomMessage] = []
        self._agent_seq_read: Dict[int, int] = {}  # agent_id -> 下一个待读的 seq（不含）
        self._next_seq: int = 0
        self._gt_room: GtRoom = gt_room

    @property
    def _agent_ids(self) -> List[int]:
        return self._gt_room.agent_ids

    @property
    def messages(self) -> List[GtRoomMessage]:
        """返回**内存窗口内**的消息（最多 `WINDOW_SIZE` 条，均已分配 seq）。

        ⚠️ 窗口化后 `len(room.messages)` **不等于房间消息总数**；需要真实总数请用
        `gtRoomMessageManager.count_room_messages(room_id)`。
        """
        return list(self._messages)

    async def append_and_assign_seq(self, msg: GtRoomMessage, *,
                                     publish: bool = False) -> None:
        """追加到消息列表并立即分配 seq —— **唯一写入路径**。

        - seq 在此处分配，不留 NULL 中间态。
        - 落库时透传 `insert_immediately`（旧实现硬编码 False，导致重启后
          「立即注入」标志丢失，消息降级为普通消息）。
        - publish=True：同时完成 WS 广播（通过 messageBus）。
        """
        msg.seq = self._next_seq
        self._next_seq += 1

        # 确保持久化
        if msg.id is None:
            db_msg = await gtRoomMessageManager.append_room_message(
                room_id=self._gt_room.id, sender_id=msg.sender_id, content=msg.content,
                send_time=msg.send_time,
                insert_immediately=bool(msg.insert_immediately), seq=msg.seq,
            )
            msg.id = db_msg.id

        self._messages.append(msg)
        self._trim_to_window()

        if publish:
            messageBus.publish(MessageBusTopic.ROOM_MSG_ADDED, gt_room=self._gt_room, gt_message=msg)

    def _trim_to_window(self) -> None:
        """把内存消息列表截断到最近 `WINDOW_SIZE` 条（最老的从头部淘汰）。"""
        overflow = len(self._messages) - self.WINDOW_SIZE
        if overflow > 0:
            del self._messages[:overflow]

    async def _collect_unread(self, agent_id: int, *, advance: bool) -> List[GtRoomMessage]:
        """取 agent_id 未读消息的**统一内核**（三处对外方法共用，避免越界逻辑写三遍）。

        两条路径：
        - 游标在窗口内（`next_seq >= 窗口下界`）：纯内存过滤，零 DB 开销；
        - 游标落后超过窗口（`next_seq < 窗口下界`）：从 DB 补读缺口
          `[next_seq, 窗口下界)` 后与内存窗口拼接 —— 这是「部分加载后未读不丢」
          的唯一保证，窗口外的未读只能靠它取回。

        补读结果**不回填内存窗口**：读一次、推进游标后不会再读第二次，
        回填反而会把窗口挤走。
        """
        next_seq = self._agent_seq_read.get(agent_id, 0)
        rows: List[GtRoomMessage]
        if self._messages and next_seq < self._messages[0].seq:
            gap = await gtRoomMessageManager.get_room_messages_by_seq_range(
                self._gt_room.id, next_seq, self._messages[0].seq,
            )
            await self._backfill_display_names(gap)
            rows = gap + [m for m in self._messages if m.seq is not None]
        else:
            rows = [m for m in self._messages if m.seq is not None and m.seq >= next_seq]
        if advance and rows:
            self._agent_seq_read[agent_id] = rows[-1].seq + 1  # type: ignore[operator]
        return rows

    async def _backfill_display_names(self, rows: List[GtRoomMessage]) -> None:
        """给补读出的 DB 消息回填 `sender_display_name`（一次批量查询，不做 N+1）。

        漏回填会让 `collect_room_messages` 用空 sender 构造 prompt 与活动记录。
        窗口内的内存对象天然带 display_name，无需回填。
        """
        sender_ids = sorted({m.sender_id for m in rows if m.sender_id is not None})
        if not sender_ids:
            return
        agents = await gtAgentManager.get_agents_by_ids(sender_ids)
        name_map = {a.id: a.display_name for a in agents if a.id is not None}
        for row in rows:
            row.sender_display_name = name_map.get(row.sender_id, "")

    async def get_unread(self, agent_id: int) -> List[GtRoomMessage]:
        """拉取 agent_id 未读的全部消息，并推进其读游标。

        **不过滤 `sender_id == agent_id`**：游标必须能越过 Agent 自己的发言，
        否则客服自己说完一句就再也读不到后续他人消息（游标被自己卡住）。
        过滤自身消息是调用方（`collect_room_messages`）的事。

        async：游标落后超过内存窗口时要查 DB 补读缺口。
        """
        return await self._collect_unread(agent_id, advance=True)

    async def has_unread_messages(self, agent_id: int) -> bool:
        """是否存在 agent_id 尚未读取的**他人**消息（只读，不推进游标）。

        必须排除自己发的消息：AI 自己刚发言完 seq 前进了一格，若把自身消息算作未读，
        调用方（Turn 结束的尾唤醒、安全边界）会误判「还有事要做」而空转。
        """
        rows = await self._collect_unread(agent_id, advance=False)
        return any(m.sender_id != agent_id for m in rows)

    async def has_unread_immediate_messages(self, agent_id: int) -> bool:
        """是否存在 agent_id 尚未读取的**他人立即注入**消息（只读，不推进游标）。

        替代旧 `has_pending_immediate_messages`。旧实现判断的是「队列里有没有
        seq=NULL 的 immediate 消息」，即「还没注入」；新实现判断的是「有条 immediate
        消息还没被读」——语义从「待注入」变成「待读取」，与 seq 解耦。

        **窗口外的 immediate 消息靠本方法的越界补读分支才能被发现**：escalate 的
        DB fallback 把窗口外消息置位后，若这里不补读，AI 永远收不到，升级形同失效。
        """
        rows = await self._collect_unread(agent_id, advance=False)
        return any(m.sender_id != agent_id and bool(m.insert_immediately) for m in rows)

    def mark_all_read(self) -> None:
        self._agent_seq_read = {aid: self._next_seq for aid in self._agent_ids}

    def inject(
        self,
        messages: List[GtRoomMessage] | None = None,
        agent_read_index: Dict[str, int] | None = None,
    ) -> None:
        """从 DB 灌入历史消息与读游标（服务启动 / 房间重建时调用）。

        这是**旁路入口**：不写库、不广播，只重建内存态，因此不走
        `append_and_assign_seq`。排序与 `_next_seq` 在此显式计算，
        不依赖调用方传入顺序（DB 查询的排序条件不该成为内存不变量的一部分）。
        """
        if messages is not None:
            self._messages = list(messages)
            self._messages.sort(
                key=lambda m: (m.seq if m.seq is not None else -1, m.id or 0)
            )
            seq_values = [m.seq for m in self._messages if m.seq is not None]
            self._next_seq = max(seq_values) + 1 if seq_values else 0
        if agent_read_index is not None:
            converted: Dict[int, int] = {}
            for k, v in agent_read_index.items():
                try:
                    converted[int(k)] = v
                except (ValueError, TypeError):
                    pass  # 忽略无效的 key
            self._agent_seq_read = converted

    async def escalate_to_immediate(self, db_id: int) -> GtRoomMessage:
        """把一条已发出的普通消息升级为「立即注入」：只置标志位，不清空 seq、不移动位置、不排序。

        顺序在消息发出那一刻就固定，升级改变的是「什么时候读」而不是「排在哪」，所以不能靠重排实现。只查内存窗口的话，Operator 对一条已被窗口淘汰的排队消息点「升级」会直接 ValueError，这是真实的功能回退；窗口外走 DB 兜底（按 id 查单条 → 校验 `room_id` → 置位），查出的对象不塞回窗口。

        拒绝条件：消息不存在或不属于本房间、SYSTEM 消息、房间内没有 AI 接收方 → ValueError；已被任一 AI 接收方读取 → RuntimeError。
        """
        msg = next((m for m in self._messages if m.id == db_id), None)
        if msg is None:
            msg = await gtRoomMessageManager.get_room_message_by_id(db_id)
            if msg is None:
                raise ValueError(f"message db_id={db_id} not found")
            # 按 id 查单条不带 room 条件，必须自行校验，否则可越权升级别的房间的消息
            if msg.room_id != self._gt_room.id:
                raise ValueError(
                    f"message db_id={db_id} does not belong to room {self._gt_room.id}"
                )

        if msg.sender_id == int(SpecialAgent.SYSTEM.value):
            raise ValueError(f"SYSTEM messages cannot be escalated, db_id={db_id}")

        ai_receivers = [
            aid for aid in self._agent_ids
            if aid > 0 and aid != msg.sender_id
        ]
        if not ai_receivers:
            raise ValueError(f"message has no AI recipient to escalate for, db_id={db_id}")

        if msg.seq is not None:
            for agent_id in ai_receivers:
                if self._agent_seq_read.get(agent_id, 0) > msg.seq:
                    raise RuntimeError(f"message db_id={db_id} already read by agent_id={agent_id}")

        msg.insert_immediately = True
        return msg

    def get_read_index(self) -> Dict[int, int]:
        """返回当前读取进度字典（供持久化使用）。"""
        return self._agent_seq_read
