"""
本地 mock LLM API 服务，用 Tornado 实现。
固定返回 send_chat_msg tool call 响应，使 agent 能走完一轮发言流程。
在独立线程中运行 IOLoop，与 pytest-asyncio 的事件循环互不干扰。
"""
import asyncio
import json
import socket
import threading
import time

import tornado.httpserver
import tornado.ioloop
import tornado.web


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ChatCompletionsHandler(tornado.web.RequestHandler):
    async def post(self):
        await asyncio.sleep(0.3)  # 模拟 LLM 响应延迟
        
        # 尝试从请求消息中提取房间名
        room_name = "general"
        try:
            body = json.loads(self.request.body)
            messages = body.get("messages", [])
            system_prompt = body.get("system_prompt", "")
            found_room = None

            # 1. 针对 V6 测试的硬编码启发式匹配 (最高优先级)
            if "alice_private" in system_prompt or "alice_private" in str(messages):
                found_room = "alice_private"
            elif "public_group" in system_prompt or "public_group" in str(messages):
                found_room = "public_group"
            elif "general" in system_prompt or "general" in str(messages):
                found_room = "general"

            # 2. 如果没匹配到，尝试正则提取
            if not found_room:
                # 从历史消息中反向寻找最近的房间线索
                for msg in reversed(messages):
                    content = msg.get("content", "")
                    if not content: continue
                    match = re.search(r"在 ([\w\.-]+@[\w\.-]+|[\w\.-]+) 房间发言", content)
                    if match:
                        val = match.group(1)
                        if "@" in val: val = val.split("@")[0]
                        if val != "roomName":
                            found_room = val
                            break
                
                if not found_room and system_prompt:
                    match = re.search(r"([\w\.-]+) 房间", system_prompt)
                    if match:
                        val = match.group(1)
                        if "@" in val: val = val.split("@")[0]
                        if val != "roomName":
                            found_room = val

            if found_room:
                room_name = found_room
        except Exception:
            pass

        response = {
            "id": "mock-response-id",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": "mock-model",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_mock_001",
                                "type": "function",
                                "function": {
                                    "name": "send_chat_msg",
                                    "arguments": json.dumps({
                                        "room_name": room_name,
                                        "msg": f"Mock LLM 在 {room_name} 的回复",
                                    }, ensure_ascii=False),
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 30,
            },
        }
        self.set_header("Content-Type", "application/json")
        self.write(json.dumps(response, ensure_ascii=False))


class MockLLMServer:
    def __init__(self):
        self.port: int = _find_free_port()
        self._ioloop: tornado.ioloop.IOLoop = None
        self._thread: threading.Thread = None
        self._started = threading.Event()

    def start(self) -> None:
        def _run():
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._ioloop = tornado.ioloop.IOLoop.current()
            app = tornado.web.Application([
                (r"/v1/chat/completions", ChatCompletionsHandler),
            ])
            server = tornado.httpserver.HTTPServer(app)
            server.listen(self.port, "127.0.0.1")
            self._started.set()
            self._ioloop.start()

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()
        self._started.wait(timeout=5)

    def stop(self) -> None:
        if self._ioloop is not None:
            self._ioloop.add_callback(self._ioloop.stop)
            self._thread.join(timeout=5)
            self._ioloop = None
