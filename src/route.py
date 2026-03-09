import tornado.web
from controller.agent_controller import AgentListHandler
from controller.room_controller import RoomListHandler, RoomMessagesHandler
from controller.ws_controller import EventsWsHandler


def make_app() -> tornado.web.Application:
    return tornado.web.Application([
        (r"/agents",                 AgentListHandler),
        (r"/rooms",                  RoomListHandler),
        (r"/rooms/([^/]+)/messages", RoomMessagesHandler),
        (r"/ws/events",              EventsWsHandler),
    ])
