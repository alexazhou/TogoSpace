import tornado.web

from controller import roleTemplateController, agentController, roomController, wsController, teamController, deptController, configController


tornado_settings = {
    'debug': False,
    'compress_response': True,
}

application = tornado.web.Application([
    # Global config
    (r"/config/frontend.json",                       configController.ConfigHandler),
    (r"/config/directories.json",                    configController.DirectoriesHandler),

    # Role templates
    (r"/role_templates/list.json",                   roleTemplateController.RoleTemplateListHandler),
    (r"/role_templates/create.json",                 roleTemplateController.RoleTemplateCreateHandler),
    (r"/role_templates/([^/]+).json",               roleTemplateController.RoleTemplateDetailHandler),
    (r"/role_templates/([^/]+)/modify.json",         roleTemplateController.RoleTemplateModifyHandler),
    (r"/role_templates/([^/]+)/delete.json",         roleTemplateController.RoleTemplateDeleteHandler),

    # Agents (运行时成员)
    (r"/agents/list.json",                          agentController.AgentListHandler),
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

], **tornado_settings)  # type: ignore [arg-type]
