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
    (r"/rooms/(\d+)/messages.json",         roomController.RoomMessagesHandler),

    # WebSocket
    (r"/ws/events.json",                    wsController.EventsWsHandler),

    # Team (配置管理)
    (r"/teams/list.json",                   teamController.TeamListHandler),
    (r"/teams/create.json",                 teamController.TeamCreateHandler),
    (r"/teams/(\d+).json",                  teamController.TeamDetailHandler),
    (r"/teams/(\d+)/modify.json",           teamController.TeamModifyHandler),
    (r"/teams/(\d+)/delete.json",           teamController.TeamDeleteHandler),

    # Team Rooms (配置管理)
    (r"/teams/(\d+)/rooms.json",            roomController.TeamRoomsHandler),
    (r"/teams/(\d+)/rooms/(\d+).json",     roomController.TeamRoomDetailHandler),
    (r"/teams/(\d+)/rooms/(\d+)/modify.json",  roomController.TeamRoomModifyHandler),
    (r"/teams/(\d+)/rooms/(\d+)/delete.json",  roomController.TeamRoomDeleteHandler),
    (r"/teams/(\d+)/rooms/(\d+)/members.json",  roomController.TeamRoomMembersHandler),
    (r"/teams/(\d+)/rooms/(\d+)/members/modify.json",  roomController.TeamRoomMembersModifyHandler),

], **tornado_settings)  # type: ignore [arg-type]
