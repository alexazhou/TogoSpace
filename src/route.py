import tornado.web
from controller.agentController import AgentListHandler
from controller.roomController import RoomListHandler, RoomMessagesHandler
from controller.wsController import EventsWsHandler


def make_app() -> tornado.web.Application:
    return tornado.web.Application([
        (r"/agents",                 AgentListHandler),
        (r"/rooms",                  RoomListHandler),
        (r"/rooms/([^/]+)/messages", RoomMessagesHandler),
        (r"/ws/events",              EventsWsHandler),
    ])
