import logging

from controller.baseController import BaseHandler
from util import configUtil

logger = logging.getLogger(__name__)


class SystemStatusHandler(BaseHandler):
    """GET /system/status.json — 返回系统运行状态（含初始化状态）。"""

    async def get(self):
        initialized = configUtil.is_initialized()
        if initialized:
            setting = configUtil.get_app_config().setting
            self.return_json({
                "initialized": True,
                "default_llm_server": setting.default_llm_server,
            })
        else:
            self.return_json({
                "initialized": False,
                "message": "当前未配置大模型服务",
            })
