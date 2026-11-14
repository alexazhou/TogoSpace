"""`mcp_servers` 表的纯 CRUD（#224，照 `gtRoleTemplateManager.py` 惯例）。

**有意不提供 upsert 默认路径**（设计稿 §5.3-④）：`gtRoleTemplateManager.save_role_template`
的 `insert().on_conflict()` 在这里是「静默覆盖别人登记的别名」——注册表把它拒绝，
覆盖必须显式走 `overwrite`（service 层先显式查、再显式改，正确性靠唯一索引兜底，
不靠那次查询）。
"""
from __future__ import annotations

import logging

import peewee
from model.dbModel.gtMcpServer import GtMcpServer

logger = logging.getLogger(__name__)

__all__ = [
    "get_by_alias",
    "list_by_team",
    "insert_entry",
    "update_entry",
    "delete_by_alias",
]


async def get_by_alias(team_id: int, alias: str) -> GtMcpServer | None:
    """按 `(team_id, alias)` 精确取一条；查的是 `alias` 列（唯一索引所在列，设计稿 §3.6/C2）。"""
    return await GtMcpServer.aio_get_or_none(
        GtMcpServer.team_id == team_id,
        GtMcpServer.alias == alias,
    )


async def list_by_team(team_id: int) -> list[GtMcpServer]:
    """本 team 全部注册项，按 alias 字典序（展示稳定，ls 两次输出可比较）。"""
    query = (
        GtMcpServer.select()
        .where(GtMcpServer.team_id == team_id)
        .order_by(GtMcpServer.alias)
    )
    return list(await query.aio_execute())


async def insert_entry(entry: GtMcpServer) -> GtMcpServer:
    """insert-only 新增。撞唯一索引（并发第二写）原样抛 `peewee.IntegrityError`，
    由 service 层统一转 `mcp_alias_exists`（与预检语义一致，设计稿 §5.3-⑥）。"""
    await entry.aio_save(force_insert=True)
    return entry


async def update_entry(entry: GtMcpServer, fields: list[str]) -> GtMcpServer:
    """按主键更新指定列（`overwrite` 显式覆盖路径专用）。"""
    await entry.aio_save(only=fields)
    return entry


async def delete_by_alias(team_id: int, alias: str) -> bool:
    """按 `(team_id, alias)` 精确删；返回是否真的删了行（不存在 ⇒ False，不静默成功）。"""
    deleted = await (
        GtMcpServer.delete()
        .where(
            GtMcpServer.team_id == team_id,
            GtMcpServer.alias == alias,
        )
        .aio_execute()
    )
    return bool(deleted)
