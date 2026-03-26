"""
本地 mock LLM API 服务，用 Tornado 实现。
支持配置响应队列，可在测试中按需设置不同响应。
队列为空时使用默认响应（send_chat_msg tool call）。
支持 OpenAI 和 Anthropic 两种格式。
在独立线程中运行 IOLoop，与 pytest-asyncio 的事件循环互不干扰。
"""
import asyncio
import json
import re
import threading
import time
from typing import Any, Dict, Optional

import tornado.httpserver
import tornado.ioloop
import tornado.web

MOCK_LLM_HOST = "127.0.0.1"
MOCK_LLM_API_PATH = "/v1/chat/completions"
MOCK_LLM_ANTHROPIC_PATH = "/v1/messages"
MOCK_LLM_PORT = 19876
MOCK_LLM_RESPONSE_DELAY_SEC = 0.05


def get_mock_llm_port() -> int:
    return MOCK_LLM_PORT


def get_mock_llm_api_url(port: int | None = None) -> str:
    return f"http://{MOCK_LLM_HOST}:{port or get_mock_llm_port()}{MOCK_LLM_API_PATH}"


def get_mock_llm_anthropic_url(port: int | None = None) -> str:
    return f"http://{MOCK_LLM_HOST}:{port or get_mock_llm_port()}{MOCK_LLM_ANTHROPIC_PATH}"


def _default_openai_response(room_name: str = "general") -> Dict[str, Any]:
    """返回 OpenAI 格式的默认 send_chat_msg tool call 响应。"""
    return {
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


def _default_anthropic_response(room_name: str = "general") -> Dict[str, Any]:
    """返回 Anthropic 格式的默认 send_chat_msg tool use 响应。"""
    return {
        "id": "msg_mock_001",
        "type": "message",
        "role": "assistant",
        "content": [
            {
                "type": "tool_use",
                "id": "toolu_mock_001",
                "name": "send_chat_msg",
                "input": {
                    "room_name": room_name,
                    "msg": f"Mock LLM 在 {room_name} 的回复",
                },
            }
        ],
        "model": "mock-model",
        "stop_reason": "tool_use",
        "usage": {
            "input_tokens": 10,
            "output_tokens": 20,
        },
    }


def _infer_room_name(
    messages: list[dict[str, Any]] | None,
    system_text: str = "",
) -> str:
    """优先使用最近消息判断房间，避免历史房间名误导当前响应。"""
    room_name = "general"
    messages = messages or []

    for msg in reversed(messages):
        content = msg.get("content", "")
        if not content:
            continue
        match = re.search(r"在 (general|alice_private|public_group) 房间发言", content)
        if match:
            return match.group(1)

    if system_text:
        match = re.search(r"(general|alice_private|public_group) 房间", system_text)
        if match:
            return match.group(1)

    flattened_messages = json.dumps(messages, ensure_ascii=False)
    if "alice_private" in flattened_messages or "alice_private" in system_text:
        return "alice_private"
    if "public_group" in flattened_messages or "public_group" in system_text:
        return "public_group"
    if "general" in flattened_messages or "general" in system_text:
        return "general"

    return room_name


class SetResponseHandler(tornado.web.RequestHandler):
    """接收响应并推入队列。支持简化格式，自动补全完整响应。"""

    async def post(self):
        body = json.loads(self.request.body)
        response = self._normalize_response(body.get("response"))
        await self.application.response_queue.put(response)
        self.set_header("Content-Type", "application/json")
        self.write(json.dumps({"status": "ok"}))

    def _normalize_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """将简化格式的响应转换为完整的 OpenAI 格式。

        支持的简化格式：
        - {"tool_calls": [{"name": "xxx", "arguments": "..."}]}
        - {"content": "text"}
        """
        # 如果已经包含完整字段，直接返回
        if "id" in response and "choices" in response:
            return response

        tool_calls = response.get("tool_calls", [])
        content = response.get("content")

        # 如果 tool_calls 是简化的格式（只包含 name 和 arguments），转换为完整格式
        if tool_calls:
            normalized_calls = []
            for i, tc in enumerate(tool_calls):
                normalized_calls.append({
                    "id": f"call_{int(time.time() * 1000)}_{i}",
                    "type": "function",
                    "function": {
                        "name": tc.get("name"),
                        "arguments": tc.get("arguments", ""),
                    }
                })
            tool_calls = normalized_calls

        # 自动补全完整响应
        return {
            "id": f"msg_{int(time.time() * 1000)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": "mock-model",
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": tool_calls if tool_calls else None,
                },
                "finish_reason": "tool_calls" if tool_calls else "stop"
            }],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 30,
            },
        }


class GetResponseHandler(tornado.web.RequestHandler):
    """从队列弹出下一个响应。"""

    async def get(self):
        queue = self.application.response_queue
        if queue.empty():
            response = None
        else:
            response = await queue.get()
        self.set_header("Content-Type", "application/json")
        self.write(json.dumps({"response": response}))


class ChatCompletionsHandler(tornado.web.RequestHandler):
    """OpenAI 格式的 chat/completions 端点。"""

    async def post(self):
        await asyncio.sleep(MOCK_LLM_RESPONSE_DELAY_SEC)

        room_name = "general"
        try:
            body = json.loads(self.request.body)
            messages = body.get("messages", [])
            system_prompt = body.get("system_prompt", "")
            room_name = _infer_room_name(messages, system_prompt)
        except Exception:
            pass

        # 从队列获取响应，队列为空时使用默认响应
        queue = self.application.response_queue
        if not queue.empty():
            response_data = await queue.get()
        else:
            response_data = _default_openai_response(room_name)

        self.set_header("Content-Type", "application/json")
        self.write(json.dumps(response_data, ensure_ascii=False))


class MessagesHandler(tornado.web.RequestHandler):
    """Anthropic 格式的 messages 端点。"""

    async def post(self):
        await asyncio.sleep(MOCK_LLM_RESPONSE_DELAY_SEC)

        room_name = "general"
        try:
            body = json.loads(self.request.body)
            messages = body.get("messages", [])
            system = body.get("system", "")
            room_name = _infer_room_name(messages, system)
        except Exception:
            pass

        # 从队列获取响应，队列为空时使用默认响应
        queue = self.application.response_queue
        if not queue.empty():
            response_data = await queue.get()
        else:
            response_data = _default_anthropic_response(room_name)

        self.set_header("Content-Type", "application/json")
        self.write(json.dumps(response_data, ensure_ascii=False))


class MockLLMServer:
    """Mock LLM API server using a fixed port for testing.

    支持动态响应队列：
    - POST /set_response - 设置响应，推入队列
    - GET /get_response - 获取下一个响应
    - POST /v1/chat/completions - OpenAI 格式的 LLM 推理端点
    - POST /v1/messages - Anthropic 格式的 LLM 推理端点
    """

    def __init__(self):
        self.port: int = MOCK_LLM_PORT
        self._ioloop: tornado.ioloop.IOLoop = None
        self._thread: threading.Thread = None
        self._started = threading.Event()
        self._server: tornado.httpserver.HTTPServer = None
        self._start_error: Optional[Exception] = None
        self._response_queue: asyncio.Queue = asyncio.Queue()

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
                    (MOCK_LLM_ANTHROPIC_PATH, MessagesHandler),
                    ("/set_response", SetResponseHandler),
                    ("/get_response", GetResponseHandler),
                ])
                app.response_queue = self._response_queue
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
