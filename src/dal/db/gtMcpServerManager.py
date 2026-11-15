"""`mcp_servers` 表的纯 CRUD（#273 全局版，照 `gtRoleTemplateManager.py` 惯例）。

**有意不提供 upsert 默认路径**：覆盖别人登记的 name 必须显式走 `overwrite`
（service 层先显式查、再显式改，正确性靠唯一索引兜底，不靠那次查询）。
"""
from __future__ import annotations

import logging

import peewee
from model.dbModel.gtMcpServer import GtMcpServer

logger = logging.getLogger(__name__)

__all__ = [
    "get_by_name",
    "list_all",
    "insert_entry",
    "update_entry",
    "delete_by_name",
]


async def get_by_name(name: str) -> GtMcpServer | None:
    """按 `name` 精确取一条（唯一索引所在列）；全局注册表，无 team 作用域。"""
    return await GtMcpServer.aio_get_or_none(
        GtMcpServer.name == name,
    )


async def list_all() -> list[GtMcpServer]:
    """全部注册项（含禁用），按 name 字典序（展示稳定，ls 两次输出可比较）。"""
    query = GtMcpServer.select().order_by(GtMcpServer.name)
    return list(await query.aio_execute())


async def insert_entry(entry: GtMcpServer) -> GtMcpServer:
    """insert-only 新增。撞唯一索引（并发第二写）原样抛 `peewee.IntegrityError`，
    由 service 层统一转 `mcp_name_exists`（与预检语义一致）。"""
    await entry.aio_save(force_insert=True)
    return entry


async def update_entry(entry: GtMcpServer, fields: list[str]) -> GtMcpServer:
    """按主键更新指定列（`overwrite` 显式覆盖 / enable 切换专用）。"""
    await entry.aio_save(only=fields)
    return entry


async def delete_by_name(name: str) -> bool:
    """按 `name` 精确删；返回是否真的删了行（不存在 ⇒ False，不静默成功）。"""
    deleted = await (
        GtMcpServer.delete()
        .where(
            GtMcpServer.name == name,
        )
        .aio_execute()
    )
    return bool(deleted)
