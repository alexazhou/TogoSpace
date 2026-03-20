"""
本地 mock LLM API 服务，用 Tornado 实现。
固定返回 send_chat_msg tool call 响应，使 agent 能走完一轮发言流程。
在独立线程中运行 IOLoop，与 pytest-asyncio 的事件循环互不干扰。
"""
import asyncio
import json
import re
import threading
import time
from typing import Optional

import tornado.httpserver
import tornado.ioloop
import tornado.web


MOCK_LLM_PORT = 19876
MOCK_LLM_HOST = "127.0.0.1"
MOCK_LLM_API_PATH = "/v1/chat/completions"
MOCK_LLM_API_URL = f"http://{MOCK_LLM_HOST}:{MOCK_LLM_PORT}{MOCK_LLM_API_PATH}"


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
                    if not content:
                        continue
                    # 匹配 "在 general 房间发言" 或 "在 alice_private 房间发言"
                    match = re.search(r"在 (general|alice_private|public_group) 房间发言", content)
                    if match:
                        found_room = match.group(1)
                        break

                if not found_room and system_prompt:
                    # 从 system_prompt 中提取房间名
                    match = re.search(r"(general|alice_private|public_group) 房间", system_prompt)
                    if match:
                        found_room = match.group(1)

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
    """Mock LLM API server using fixed port for testing."""

    def __init__(self):
        self.port: int = MOCK_LLM_PORT
        self._ioloop: tornado.ioloop.IOLoop = None
        self._thread: threading.Thread = None
        self._started = threading.Event()
        self._server: tornado.httpserver.HTTPServer = None
        self._start_error: Optional[Exception] = None

    def start(self) -> None:
        self._started.clear()
        self._start_error = None

        def _run():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._ioloop = tornado.ioloop.IOLoop.current()
                app = tornado.web.Application([
                    (MOCK_LLM_API_PATH, ChatCompletionsHandler),
                ])
                self._server = tornado.httpserver.HTTPServer(app)
                self._server.listen(self.port, MOCK_LLM_HOST)
                self._started.set()
                self._ioloop.start()
            except Exception as exc:  # pragma: no cover - 仅在异常启动场景触发
                self._start_error = exc
                self._started.set()

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()
        if not self._started.wait(timeout=5):
            raise RuntimeError(f"MockLLM 启动超时（{self.port}）")
        if self._start_error is not None:
            raise RuntimeError(f"MockLLM 启动失败（{self.port}）: {self._start_error}") from self._start_error

    def stop(self) -> None:
        if self._ioloop is not None:
            def _shutdown():
                if self._server:
                    self._server.stop()
                self._ioloop.stop()
            self._ioloop.add_callback(_shutdown)
            self._thread.join(timeout=5)
            self._ioloop = None
            self._server = None
        self._thread = None
        self._started.clear()
        self._start_error = None
