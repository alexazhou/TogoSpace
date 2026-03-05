from service.chat_room_service import ChatRoom


class TestChatRoom:
    def setup_method(self):
        self.room = ChatRoom("test_room")

    def test_add_message(self):
        self.room.add_message("alice", "你好")
        assert len(self.room.messages) == 1
        assert self.room.messages[0].sender == "alice"
        assert self.room.messages[0].content == "你好"

    def test_get_context_empty(self):
        assert self.room.get_context() == ""

    def test_get_context_format(self):
        self.room.add_message("alice", "你好")
        self.room.add_message("bob", "世界")
        assert self.room.get_context() == "alice: 你好\nbob: 世界"

    def test_get_context_max_messages(self):
        for i in range(5):
            self.room.add_message("user", f"消息{i}")
        context = self.room.get_context(max_messages=3)
        lines = context.split("\n")
        assert len(lines) == 3
        assert "消息2" in lines[0]
        assert "消息4" in lines[2]

    def test_get_context_messages_system_role(self):
        self.room.add_message("system", "你是一个助手")
        msgs = self.room.get_context_messages()
        assert len(msgs) == 1
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == "你是一个助手"

    def test_get_context_messages_user_role(self):
        self.room.add_message("alice", "你好")
        msgs = self.room.get_context_messages()
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "alice: 你好"

    def test_get_context_messages_mixed(self):
        self.room.add_message("system", "系统提示")
        self.room.add_message("agent1", "代理消息")
        msgs = self.room.get_context_messages()
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"

    def test_format_log(self):
        self.room.add_message("alice", "你好")
        self.room.add_message("bob", "世界")
        log = self.room.format_log()
        assert "test_room" in log
        assert "alice" in log
        assert "你好" in log
        assert "bob" in log
        assert "世界" in log
