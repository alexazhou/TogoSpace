from __future__ import annotations

import peewee

from .base import DbModelBase


class GtAgentContainer(DbModelBase):
    """Agent 自建容器记录（只记录 `create_container` 创建的容器）。

    外部 / 他人容器不入库：它们不进配额，也不会出现在 `list_containers` 里。

    设计要点见 docs/design/2026-10-31-docker-agent-sandbox/step2_technical.md §2.1：
    - `container_id` 存 Docker 的 12 位短 ID，不另造系统 ID；
    - 创建流程**先插入 `container_id = NULL` 的预记录**占用配额与名称，`docker create`
      成功后再回填，因此该列必须可空；
    - 运行状态不落库（实时 `docker inspect`），当前执行环境也不落库（只在内存）；
    - 软删除：`deleted = 1`，不物理删除行，便于链路排查与执行回溯。
    """

    agent_id:       int          = peewee.IntegerField()
    container_id:   str | None   = peewee.CharField(null=True)
    container_name: str          = peewee.CharField()
    image:          str          = peewee.CharField()
    deleted:        int          = peewee.IntegerField(default=0)

    class Meta:
        table_name = "agent_containers"
        indexes = (
            (("agent_id", "deleted"), False),
        )
