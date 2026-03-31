from __future__ import annotations

from dataclasses import dataclass
from typing import List

import peewee

from .base import DbModelBase, JsonField


@dataclass(frozen=True)
class DeptRoomSpec:
    biz_id: str
    name: str
    initial_topic: str
    member_ids: list[int]
    max_turns: int | None = None


class GtDept(DbModelBase):
    team_id:        int            = peewee.IntegerField()
    name:           str            = peewee.CharField()
    responsibility: str            = peewee.TextField(default="")
    parent_id:      int            = peewee.IntegerField(null=True)
    manager_id:     int            = peewee.IntegerField()
    agent_ids:      list[int]      = JsonField(default=list)

    # 非数据库字段，用于构建树结构
    children: List["GtDept"] = []

    class Meta:
        table_name = "depts"
        indexes = ((("team_id", "name"), True),)

    def collect_room_specs(self) -> list[DeptRoomSpec]:
        room_specs: list[DeptRoomSpec] = []
        self._append_room_specs(room_specs)
        return room_specs

    def _append_room_specs(self, room_specs: list[DeptRoomSpec]) -> None:
        assert self.id is not None, "dept.id must be set before collecting room specs"
        room_specs.append(DeptRoomSpec(
            biz_id=f"DEPT:{self.id}",
            name=self.name,
            initial_topic=self.responsibility or f"{self.name} 部门群聊",
            member_ids=list(dict.fromkeys(self.agent_ids)),
        ))
        for child in self.children:
            child._append_room_specs(room_specs)

__all__ = ["GtDept", "DeptRoomSpec"]
