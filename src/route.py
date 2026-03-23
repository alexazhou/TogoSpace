import tornado.web

from controller import agentController, roomController, wsController, teamController


tornado_settings = {
    'debug': False,
    'compress_response': True,
}

application = tornado.web.Application([
    # Agent
    (r"/agents/list.json",                  agentController.AgentListHandler),
    (r"/teams/(\d+)/agents/([^/]+).json",   agentController.AgentDetailHandler),

    # Room (运行时)
    (r"/rooms/list.json",                   roomController.RoomListHandler),
    (r"/rooms/(\d+)/messages/list.json",    roomController.RoomMessagesHandler),
    (r"/rooms/(\d+)/messages/send.json",    roomController.RoomMessagesHandler),

    # WebSocket
    (r"/ws/events.json",                    wsController.EventsWsHandler),

    # Team (配置管理)
    (r"/teams/list.json",                   teamController.TeamListHandler),
    (r"/teams/create.json",                 teamController.TeamCreateHandler),
    (r"/teams/(\d+).json",                  teamController.TeamDetailHandler),
    (r"/teams/(\d+)/modify.json",           teamController.TeamModifyHandler),
    (r"/teams/(\d+)/delete.json",           teamController.TeamDeleteHandler),

    # Team Rooms (配置管理)
    (r"/teams/(\d+)/rooms/list.json",       roomController.TeamRoomsHandler),
    (r"/teams/(\d+)/rooms/create.json",     roomController.TeamRoomCreateHandler),
    (r"/teams/(\d+)/rooms/(\d+).json",     roomController.TeamRoomDetailHandler),
    (r"/teams/(\d+)/rooms/(\d+)/modify.json",  roomController.TeamRoomModifyHandler),
    (r"/teams/(\d+)/rooms/(\d+)/delete.json",  roomController.TeamRoomDeleteHandler),
    (r"/teams/(\d+)/rooms/(\d+)/members/list.json",  roomController.TeamRoomMembersHandler),
    (r"/teams/(\d+)/rooms/(\d+)/members/modify.json",  roomController.TeamRoomMembersModifyHandler),

], **tornado_settings)  # type: ignore [arg-type]
