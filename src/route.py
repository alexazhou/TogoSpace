import tornado.web
from controller.agentController import AgentListHandler
from controller.roomController import RoomListHandler, RoomMessagesHandler
from controller.wsController import EventsWsHandler
from controller.teamController import (
    TeamListHandler,
    TeamDetailHandler,
    TeamRoomsHandler,
    TeamRoomDetailHandler,
    RoomMembersHandler,
)


def make_app() -> tornado.web.Application:
    return tornado.web.Application([
        (r"/agents",                 AgentListHandler),
        (r"/rooms",                  RoomListHandler),
        (r"/rooms/([^/]+)/messages", RoomMessagesHandler),
        (r"/ws/events",              EventsWsHandler),
        (r"/teams",                  TeamListHandler),
        (r"/teams/([^/]+)",          TeamDetailHandler),
        (r"/teams/([^/]+)/rooms",   TeamRoomsHandler),
        (r"/teams/([^/]+)/rooms/([^/]+)",            TeamRoomDetailHandler),
        (r"/teams/([^/]+)/rooms/([^/]+)/members",   RoomMembersHandler),
    ])
