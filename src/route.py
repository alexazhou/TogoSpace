import tornado.web

from controller import agentController, roomController, wsController, teamController


tornado_settings = {
    'debug': False,
    'compress_response': True,
}

application = tornado.web.Application([
    # Agent
    (r"/agents.json",                       agentController.AgentListHandler),

    # Room (运行时)
    (r"/rooms.json",                        roomController.RoomListHandler),
    (r"/rooms/([^/]+)/messages.json",       roomController.RoomMessagesHandler),

    # WebSocket
    (r"/ws/events.json",                    wsController.EventsWsHandler),

    # Team (配置管理)
    (r"/teams/list.json",                   teamController.TeamListHandler),
    (r"/teams/create.json",                 teamController.TeamCreateHandler),
    (r"/teams/([^/]+).json",                teamController.TeamDetailHandler),
    (r"/teams/([^/]+)/modify.json",         teamController.TeamModifyHandler),
    (r"/teams/([^/]+)/delete.json",         teamController.TeamDeleteHandler),

    # Team Rooms (配置管理)
    (r"/teams/([^/]+)/rooms.json",          roomController.TeamRoomsHandler),
    (r"/teams/([^/]+)/rooms/([^/]+).json",               roomController.TeamRoomDetailHandler),
    (r"/teams/([^/]+)/rooms/([^/]+)/modify.json",          roomController.TeamRoomModifyHandler),
    (r"/teams/([^/]+)/rooms/([^/]+)/delete.json",          roomController.TeamRoomDeleteHandler),
    (r"/teams/([^/]+)/rooms/([^/]+)/members.json",        roomController.RoomMembersHandler),

], **tornado_settings)  # type: ignore [arg-type]
