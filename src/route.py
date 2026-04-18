import datetime
import os

import tornado.web

from controller import roleTemplateController, agentController, roomController, wsController, teamController, deptController, configController, activityController, settingController, systemController, initController

import sys as _sys
if getattr(_sys, "frozen", False):
    _FRONTEND_DIST = os.path.join(_sys._MEIPASS, "assets/frontend")
else:
    _FRONTEND_DIST = os.path.join(os.path.dirname(__file__), "../assets/frontend")


class _SPAHandler(tornado.web.StaticFileHandler):
    """Vue SPA fallback：文件不存在时回退到 index.html。"""

    async def get(self, path: str, include_body: bool = True) -> None:
        try:
            await super().get(path, include_body)
        except tornado.web.HTTPError as e:
            if e.status_code == 404:
                await super().get("index.html", include_body)
            else:
                raise

    def get_cache_time(self, path: str, modified: datetime.datetime | None, mime_type: str) -> int:
        # index.html 不缓存，保证每次都拿到最新版本（避免 Safari 等激进缓存导致加载旧页面）
        if path == "index.html":
            return 0
        return super().get_cache_time(path, modified, mime_type)

    def set_extra_headers(self, path: str) -> None:
        if path == "index.html":
            # 对 SPA 壳文件使用强 no-store，避免 Safari 等浏览器继续复用旧页面。
            self.set_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.set_header("Pragma", "no-cache")
            self.set_header("Expires", "0")
            self.clear_header("Etag")
            self.clear_header("Last-Modified")


tornado_settings = {
    'debug': False,
    'compress_response': True,
    # WebSocket 心跳配置（Tornado 内置）
    'websocket_ping_interval': 30,   # 每 30 秒发送一次 ping
    'websocket_ping_timeout': 30,     # 30 秒未收到 pong 则关闭连接（不能超过 ping_interval）
}

application = tornado.web.Application([
    # Global config
    (r"/config/frontend.json",                       configController.ConfigHandler),
    (r"/config/directories.json",                    configController.DirectoriesHandler),

    # LLM Service Config (V12)
    (r"/config/llm_services/list.json",              settingController.LlmServiceListHandler),
    (r"/config/llm_services/create.json",            settingController.LlmServiceCreateHandler),
    (r"/config/llm_services/test.json",              settingController.LlmServiceTestHandler),
    (r"/config/llm_services/(\d+)/modify.json",      settingController.LlmServiceModifyHandler),
    (r"/config/llm_services/(\d+)/delete.json",      settingController.LlmServiceDeleteHandler),
    (r"/config/llm_services/(\d+)/set_default.json",  settingController.LlmServiceSetDefaultHandler),
    (r"/config/language.json",                       settingController.LanguageHandler),

    # System Status & Quick Init (V13)
    (r"/system/status.json",                         systemController.SystemStatusHandler),
    (r"/config/quick_init.json",                     initController.QuickInitHandler),

    # Role templates
    (r"/role_templates/list.json",                   roleTemplateController.RoleTemplateListHandler),
    (r"/role_templates/create.json",                 roleTemplateController.RoleTemplateCreateHandler),
    (r"/role_templates/([^/]+).json",               roleTemplateController.RoleTemplateDetailHandler),
    (r"/role_templates/([^/]+)/modify.json",         roleTemplateController.RoleTemplateModifyHandler),
    (r"/role_templates/([^/]+)/delete.json",         roleTemplateController.RoleTemplateDeleteHandler),

    # Agents (运行时成员)
    (r"/agents/list.json",                          agentController.AgentListHandler),
    (r"/agents/(\d+).json",                         agentController.AgentDetailByIdHandler),
    (r"/agents/(\d+)/resume.json",                  agentController.AgentResumeHandler),
    (r"/agents/(\d+)/stop.json",                    agentController.AgentStopHandler),
    (r"/teams/(\d+)/agents/batch_update.json",      agentController.AgentBatchUpdateHandler),
    (r"/teams/(\d+)/agents/save.json",              agentController.TeamAgentsSaveHandler),
    (r"/teams/(\d+)/agents/([^/]+).json",           agentController.AgentDetailHandler),

    # Room (运行时)
    (r"/rooms/list.json",                           roomController.RoomListHandler),
    (r"/rooms/(\d+)/messages/list.json",            roomController.RoomMessagesHandler),
    (r"/rooms/(\d+)/messages/send.json",            roomController.RoomMessagesHandler),

    # WebSocket
    (r"/ws/events.json",                            wsController.EventsWsHandler),

    # Team (配置管理)
    (r"/teams/list.json",                           teamController.TeamListHandler),
    (r"/teams/create.json",                         teamController.TeamCreateHandler),
    (r"/teams/(\d+).json",                          teamController.TeamDetailHandler),
    (r"/teams/(\d+)/modify.json",                   teamController.TeamModifyHandler),
    (r"/teams/(\d+)/delete.json",                   teamController.TeamDeleteHandler),
    (r"/teams/(\d+)/set_enabled.json",              teamController.TeamSetEnabledHandler),
    (r"/teams/(\d+)/clear_data.json",               teamController.TeamClearDataHandler),

    # Team Rooms (配置管理)
    (r"/teams/(\d+)/rooms/list.json",               roomController.TeamRoomsHandler),
    (r"/teams/(\d+)/rooms/create.json",             roomController.TeamRoomCreateHandler),
    (r"/teams/(\d+)/rooms/(\d+).json",              roomController.TeamRoomDetailHandler),
    (r"/teams/(\d+)/rooms/(\d+)/modify.json",       roomController.TeamRoomModifyHandler),
    (r"/teams/(\d+)/rooms/(\d+)/delete.json",       roomController.TeamRoomDeleteHandler),
    (r"/teams/(\d+)/rooms/(\d+)/agents/list.json",  roomController.TeamRoomAgentsHandler),
    (r"/teams/(\d+)/rooms/(\d+)/agents/modify.json",roomController.TeamRoomAgentsModifyHandler),

    # Dept Tree (V10)
    (r"/teams/(\d+)/dept_tree.json",                deptController.DeptTreeDetailHandler),
    (r"/teams/(\d+)/dept_tree/update.json",         deptController.DeptTreeUpdateHandler),

    # Activities (V11)
    (r"/activities.json",                            activityController.ActivitiesHandler),
    (r"/agents/(\d+)/activities.json",               activityController.AgentActivitiesHandler),
    (r"/teams/(\d+)/activities.json",                activityController.TeamActivitiesHandler),

    # 前端静态文件（必须放最后，SPA fallback）
    (r"/(.*)", _SPAHandler, {"path": _FRONTEND_DIST, "default_filename": "index.html"}),

], **tornado_settings)  # type: ignore [arg-type]
