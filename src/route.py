import os

import tornado.web

from controller import roleTemplateController, agentController, roomController, wsController, teamController, deptController, configController, activityController, settingController

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


tornado_settings = {
    'debug': False,
    'compress_response': True,
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
