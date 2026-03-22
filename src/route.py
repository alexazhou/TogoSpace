import tornado.web

from controller import agentController, roomController, wsController, teamController


tornado_settings = {
    'debug': False,
    'compress_response': True,
}

application = tornado.web.Application([
    (r"/agents",                       agentController.AgentListHandler),
    (r"/rooms",                        roomController.RoomListHandler),
    (r"/rooms/([^/]+)/messages",       roomController.RoomMessagesHandler),
    (r"/ws/events",                    wsController.EventsWsHandler),
    (r"/teams",                        teamController.TeamListHandler),
    (r"/teams/([^/]+)",                teamController.TeamDetailHandler),
    (r"/teams/([^/]+)/rooms",          roomController.TeamRoomsHandler),
    (r"/teams/([^/]+)/rooms/([^/]+)",               roomController.TeamRoomDetailHandler),
    (r"/teams/([^/]+)/rooms/([^/]+)/members",        roomController.RoomMembersHandler),
], **tornado_settings)  # type: ignore [arg-type]
