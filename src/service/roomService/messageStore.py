from __future__ import annotations

import logging
from typing import Dict, List

from dal.db import gtRoomMessageManager

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
        """返回房间内全部消息（均已分配 seq）。"""
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

        if publish:
            messageBus.publish(MessageBusTopic.ROOM_MSG_ADDED, gt_room=self._gt_room, gt_message=msg)

    def get_unread(self, agent_id: int) -> List[GtRoomMessage]:
        """拉取 agent_id 未读的全部消息，并推进其读游标。

        **不过滤 `sender_id == agent_id`**：游标必须能越过 Agent 自己的发言，
        否则客服自己说完一句就再也读不到后续他人消息（游标被自己卡住）。
        过滤自身消息是调用方（`collect_room_messages`）的事。
        """
        next_seq = self._agent_seq_read.get(agent_id, 0)
        new_msgs = [m for m in self._messages if m.seq is not None and m.seq >= next_seq]
        if new_msgs:
            self._agent_seq_read[agent_id] = new_msgs[-1].seq + 1  # type: ignore[operator]
        return new_msgs

    def has_unread_messages(self, agent_id: int) -> bool:
        """是否存在 agent_id 尚未读取的**他人**消息（只读，不推进游标）。

        必须排除自己发的消息：AI 自己刚发言完 seq 前进了一格，若把自身消息算作未读，
        调用方（Turn 结束的尾唤醒、安全边界）会误判「还有事要做」而空转。
        """
        next_seq = self._agent_seq_read.get(agent_id, 0)
        return any(
            m.seq is not None and m.seq >= next_seq and m.sender_id != agent_id
            for m in self._messages
        )

    def has_unread_immediate_messages(self, agent_id: int) -> bool:
        """是否存在 agent_id 尚未读取的**他人立即注入**消息（只读，不推进游标）。

        替代旧 `has_pending_immediate_messages`。旧实现判断的是「队列里有没有
        seq=NULL 的 immediate 消息」，即「还没注入」；新实现判断的是「有条 immediate
        消息还没被读」——语义从「待注入」变成「待读取」，与 seq 解耦。
        """
        next_seq = self._agent_seq_read.get(agent_id, 0)
        return any(
            m.seq is not None and m.seq >= next_seq
            and m.sender_id != agent_id
            and m.insert_immediately
            for m in self._messages
        )

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

    def escalate_to_immediate(self, db_id: int) -> GtRoomMessage:
        """把一条已发出的普通消息升级为「立即注入」。

        升级**只置标志位**：不清空 seq、不移动位置、不排序。
        顺序在消息发出的那一刻就固定了，升级改变的是「什么时候读」，不是「排在哪」。

        拒绝条件：
        - 消息不存在 → ValueError
        - 非 OPERATOR 消息 → ValueError（只有操作者的排队消息可以升级，
          Agent 自己的发言与系统消息没有「立即引导」语义）
        - 已被任一 AI Agent 读取 → RuntimeError（读都读过了，再升级无意义）
        """
        msg = next((m for m in self._messages if m.id == db_id), None)
        if msg is None:
            raise ValueError(f"message db_id={db_id} not found")

        if msg.sender_id != int(SpecialAgent.OPERATOR.value):
            raise ValueError(
                f"only OPERATOR messages can be escalated, db_id={db_id} sender_id={msg.sender_id}"
            )

        if msg.seq is not None:
            for agent_id in self._agent_ids:
                if agent_id == int(SpecialAgent.OPERATOR.value):
                    continue
                if self._agent_seq_read.get(agent_id, 0) > msg.seq:
                    raise RuntimeError(f"message db_id={db_id} already read by agent_id={agent_id}")

        msg.insert_immediately = True
        return msg

    def get_read_index(self) -> Dict[int, int]:
        """返回当前读取进度字典（供持久化使用）。"""
        return self._agent_seq_read
