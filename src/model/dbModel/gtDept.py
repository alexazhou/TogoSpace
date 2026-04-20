from __future__ import annotations

from dataclasses import dataclass
from typing import List

import peewee
from playhouse.shortcuts import model_to_dict

from .base import DbModelBase, JsonField


@dataclass(frozen=True)
class DeptRoomSpec:
    biz_id: str
    name: str
    initial_topic: str
    agent_ids: list[int]
    max_turns: int | None = None
    i18n: dict | None = None


class GtDept(DbModelBase):
    team_id:        int            = peewee.IntegerField()
    name:           str            = peewee.CharField()
    responsibility: str            = peewee.TextField(default="")
    parent_id:      int            = peewee.IntegerField(null=True)
    manager_id:     int            = peewee.IntegerField()
    agent_ids:      list[int]      = JsonField(default=list)
    i18n:           dict           = JsonField(default=dict)  # 多语言数据，含 dept_name, responsibility

    # 非数据库字段，用于构建树结构
    children: List["GtDept"] = []

    class Meta:
        table_name = "depts"
        indexes = ((("team_id", "name"), True),)

    def to_json(self) -> dict:
        """转换为 JSON 可序列化的字典，包含非数据库字段 children。"""
        result = model_to_dict(self)
        if self.children is not None:
            result["children"] = [child.to_json() for child in self.children]
        return result

    def validate_and_collect_tree_ids(self) -> tuple[set[int], set[int]]:
        if len(self.agent_ids) < 2:
            raise ValueError(f"部门 '{self.name}' 成员不足 2 人，无法创建房间")

        agent_ids: set[int] = set(self.agent_ids)
        dept_ids: set[int] = self.collect_dept_ids()

        for child in self.children:
            child_agent_ids, _ = child.validate_and_collect_tree_ids()
            agent_ids.update(child_agent_ids)

        return agent_ids, dept_ids

    def collect_dept_ids(self) -> set[int]:
        dept_ids: set[int] = {self.id} if self.id is not None else set()
        for child in self.children:
            dept_ids.update(child.collect_dept_ids())
        return dept_ids

    def collect_room_specs(self) -> list[DeptRoomSpec]:
        room_specs: list[DeptRoomSpec] = []
        self._append_room_specs(room_specs)
        return room_specs

    def _append_room_specs(self, room_specs: list[DeptRoomSpec]) -> None:
        assert self.id is not None, "dept.id must be set before collecting room specs"

        # 构建部门房间的 i18n 配置
        dept_i18n = self.i18n or {}
        dept_name_i18n = dept_i18n.get("dept_name") or {}

        # 生成多语言的 initial_topic
        initial_topic_i18n = {}
        # 中文版本：使用中文部门名
        zh_dept_name = dept_name_i18n.get("zh-CN") or self.name
        initial_topic_i18n["zh-CN"] = f"这里是{zh_dept_name}部门的公共群聊，部门人员可在这里互相沟通。"
        # 英文版本：使用英文部门名
        en_dept_name = dept_name_i18n.get("en") or self.name
        initial_topic_i18n["en"] = f"This is the public group chat for the {en_dept_name} department. Department members can communicate here."

        # 房间 i18n：display_name 与 dept_name_i18n 相同，加上 initial_topic
        room_i18n = {
            "display_name": dept_name_i18n,
            "initial_topic": initial_topic_i18n,
        }

        room_specs.append(DeptRoomSpec(
            biz_id=f"DEPT:{self.id}",
            name=self.name,
            initial_topic=initial_topic_i18n.get("zh-CN") or f"这里是{self.name}部门的公共群聊，部门人员可在这里互相沟通。",
            agent_ids=list(dict.fromkeys(self.agent_ids)),
            i18n=room_i18n,
        ))
        for child in self.children:
            child._append_room_specs(room_specs)

__all__ = ["GtDept", "DeptRoomSpec"]
