import logging

import appPaths
from controller.baseController import BaseHandler
from service import schedulerService
from util import configUtil

logger = logging.getLogger(__name__)


class SystemStatusHandler(BaseHandler):
    """GET /system/status.json — 返回系统运行状态（含初始化状态）。"""

    async def get(self):
        initialized = configUtil.is_initialized()
        schedule_state = schedulerService.get_schedule_state()
        setting = configUtil.get_app_config().setting
        demo_mode = setting.demo_mode
        demo_flags = {
            "demo_mode": demo_mode.enabled,
            "freeze_data": demo_mode.read_only,
            "read_only": demo_mode.read_only,
            "hide_sensitive_info": demo_mode.hide_sensitive,
            "development_mode": appPaths.IS_DEV_MODE,
        }
        if initialized:
            self.return_json({
                "initialized": True,
                "default_llm_server": setting.default_llm_server,
                "schedule_state": schedule_state,
                "language": configUtil.get_language(),
                **demo_flags,
            })
        else:
            self.return_json({
                "initialized": False,
                "message": "当前未配置大模型服务",
                "schedule_state": schedule_state,
                "language": configUtil.get_language(),
                **demo_flags,
            })
