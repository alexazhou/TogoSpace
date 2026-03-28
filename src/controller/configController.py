from controller.baseController import BaseHandler
from util import configUtil


# 系统支持的 driver 类型列表
DRIVER_TYPES = [
    {"name": "native", "description": "原生 OpenAI API 驱动"},
    {"name": "claude_sdk", "description": "Claude Agent SDK 驱动"},
    {"name": "tsp", "description": "TSP 协议驱动"},
]


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

        self.return_json({
            "models": models,
            "driver_types": DRIVER_TYPES,
            "default_model": setting.default_llm_server,
        })