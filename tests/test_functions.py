from service.chat_room_service import ChatRoom
from util.functions_util import get_weather, get_time, calculate, send_chat_msg


class TestFunctions:
    def setup_method(self):
        self.room = ChatRoom("test_room")

    def test_get_weather_celsius(self):
        result = get_weather("北京", "celsius")
        assert "25°C" in result

    def test_get_weather_fahrenheit(self):
        result = get_weather("北京", "fahrenheit")
        assert "77°F" in result

    def test_get_time_local(self):
        result = get_time()
        assert "当前本地时间" in result

    def test_get_time_with_timezone(self):
        result = get_time(timezone="UTC")
        assert "UTC" in result

    def test_calculate_valid(self):
        result = calculate("2 + 3")
        assert "5" in result

    def test_calculate_invalid(self):
        result = calculate("import os")
        assert "计算错误" in result

    def test_send_chat_msg_returns_success(self):
        result = send_chat_msg("some_room", "hello")
        assert result == "success"

    def test_send_chat_msg_adds_to_chat_room(self):
        result = send_chat_msg("some_room", "hello", _chat_room=self.room, _agent_name="agent1")
        assert result == "success"
        assert len(self.room.messages) == 1
        assert self.room.messages[0].sender == "agent1"
        assert self.room.messages[0].content == "hello"
