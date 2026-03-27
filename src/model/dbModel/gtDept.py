from __future__ import annotations

import peewee

from .base import DbModelBase, JsonField


class GtDept(DbModelBase):
    team_id:        int       = peewee.IntegerField()
    name:           str       = peewee.CharField()
    responsibility: str       = peewee.TextField(default="")
    parent_id:      int       = peewee.IntegerField(null=True)
    manager_id:     int       = peewee.IntegerField()
    agent_ids:      list[int] = JsonField(default=list)

    class Meta:
        table_name = "depts"
        indexes = ((("team_id", "name"), True),)


__all__ = ["GtDept"]
