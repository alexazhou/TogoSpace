import pytest
from typing import Optional, Literal
from service.chat_room_service import ChatRoom
from util.function_loader_util import python_type_to_json_schema, get_function_metadata
from service.function_service import execute_function
from util.functions_util import get_weather, send_chat_msg


class TestFunctionLoader:
    def setup_method(self):
        self.room = ChatRoom("test_room")

    def test_type_to_schema_str(self):
        assert python_type_to_json_schema(str) == {"type": "string"}

    def test_type_to_schema_int(self):
        assert python_type_to_json_schema(int) == {"type": "integer"}

    def test_type_to_schema_optional(self):
        assert python_type_to_json_schema(Optional[str]) == {"type": "string"}

    def test_type_to_schema_literal(self):
        result = python_type_to_json_schema(Literal["a", "b"])
        assert result == {"enum": ["a", "b"]}

    def test_get_metadata_excludes_private_params(self):
        metadata = get_function_metadata("send_chat_msg", send_chat_msg)
        properties = metadata["parameters"]["properties"]
        assert "_chat_room" not in properties
        assert "_agent_name" not in properties

    def test_get_metadata_required_params(self):
        metadata = get_function_metadata("get_weather", get_weather)
        required = metadata["parameters"]["required"]
        assert "location" in required

    def test_execute_function_basic(self):
        result = execute_function("get_weather", {"location": "北京", "unit": "celsius"})
        assert "25°C" in result

    def test_execute_function_with_context(self):
        context = {"chat_room": self.room, "agent_name": "agent1"}
        result = execute_function("send_chat_msg", {"chat_windows_name": "room1", "msg": "hello"}, context=context)
        assert result == "success"
        assert len(self.room.messages) == 1
        assert self.room.messages[0].content == "hello"

    def test_execute_function_not_found(self):
        with pytest.raises(ValueError):
            execute_function("nonexistent_function", {})

    def test_execute_function_bad_args(self):
        with pytest.raises(ValueError):
            execute_function("get_weather", {"bad_param": "value"})
