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

        # 提取可用模型列表
        models = [
            {
                "name": s.name,
                "model": s.model,
                "enabled": s.enable,
            }
            for s in setting.llm_services
        ]

        # 提取 driver 类型列表
        driver_types = [
            {"name": dt.name, "description": _get_driver_description(dt)}
            for dt in DriverType
        ]

        self.return_json({
            "models": models,
            "driver_types": driver_types,
            "default_model": setting.default_llm_server,
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
