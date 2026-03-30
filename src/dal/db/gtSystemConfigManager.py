from __future__ import annotations

from constants import SystemConfigKey
from model.dbModel.gtSystemConfig import GtSystemConfig


async def get_config(key: SystemConfigKey) -> str | None:
    """获取指定配置项的值。"""
    row = await GtSystemConfig.aio_get_or_none(GtSystemConfig.key == key)
    return row.value if row else None


async def set_config(key: SystemConfigKey, value: str) -> None:
    """设置指定配置项的值。"""
    await (
        GtSystemConfig.insert(key=key, value=value)
        .on_conflict(
            conflict_target=[GtSystemConfig.key],
            update={
                GtSystemConfig.value: value,
            },
        )
        .aio_execute()
    )


__all__ = ["get_config", "set_config"]
