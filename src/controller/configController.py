import os
from constants import DriverType
from controller.baseController import BaseHandler
from util import configUtil
import appPaths


class ConfigHandler(BaseHandler):
    """GET /config/frontend.json - 获取前端所需的全局配置"""

    async def get(self) -> None:
        app_config = configUtil.get_app_config()
        setting = app_config.setting

        # 提取 driver 类型列表
        driver_types = [
            {"name": dt.name, "description": _get_driver_description(dt)}
            for dt in DriverType
        ]

        # 构建模型槽位选项
        dm = setting.default_models
        model_slots = [
            {"key": "primary", "value": dm.primary},
            {"key": "lite", "value": dm.lite},
            {"key": "advanced", "value": dm.advanced},
            {"key": "vision", "value": dm.vision},
        ]

        # 上下文配置默认值（未设置时使用的值）
        from util.configTypes import LlmContextConfig
        context_config_defaults = LlmContextConfig().model_dump(mode="json")

        self.return_json({
            "driver_types": driver_types,
            "model_slots": model_slots,
            "context_config_defaults": context_config_defaults,
            "demo_mode": setting.demo_mode,
        })


class DirectoriesHandler(BaseHandler):
    """GET /config/directories.json - 获取系统目录配置"""

    async def get(self) -> None:
        demo_mode = configUtil.get_app_config().setting.demo_mode
        if demo_mode.hide_sensitive:
            directories = {
                "storage_root": "",
                "config_dir": "",
                "workspace_dir": "",
                "data_dir": "",
                "log_dir": "",
            }
        else:
            directories = {
                "storage_root": appPaths.STORAGE_ROOT,
                "config_dir": appPaths.CONFIG_DIR,
                "workspace_dir": appPaths.WORKSPACE_ROOT,
                "data_dir": appPaths.DATA_DIR,
                "log_dir": appPaths.LOGS_DIR,
            }
        self.return_json({
            **directories,
            "demo_mode": configUtil.get_app_config().setting.demo_mode,
        })


def _get_driver_description(driver_type: DriverType) -> str:
    descriptions = {
        DriverType.NATIVE: "原生 OpenAI API 驱动",
        DriverType.CLAUDE_SDK: "Claude Agent SDK 驱动",
        DriverType.TSP: "TSP 协议驱动",
    }
    return descriptions.get(driver_type, "")
