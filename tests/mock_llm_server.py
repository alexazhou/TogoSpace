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


def _default_openai_response(room_name: str = "general", with_send: bool = True) -> Dict[str, Any]:
    """返回 OpenAI 格式的默认响应。

    with_send=True：send_chat_msg + finish_chat_turn（有 Operator 消息时）
    with_send=False：finish_chat_turn only（仅 SYSTEM 初始化消息时，避免污染房间历史）
    """
    tool_calls: list[Dict[str, Any]] = []
    if with_send:
        tool_calls.append({
            "id": "call_mock_001",
            "type": "function",
            "function": {
                "name": "send_chat_msg",
                "arguments": json.dumps({
                    "room_name": room_name,
                    "msg": f"Mock LLM 在 {room_name} 的回复",
                }, ensure_ascii=False),
            },
        })
    tool_calls.append({
        "id": "call_mock_002",
        "type": "function",
        "function": {
            "name": "finish_chat_turn",
            "arguments": json.dumps({"confirm_no_need_talk": not with_send}),
        },
    })
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
                    "tool_calls": tool_calls,
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


def _has_operator_message(messages: list[Dict[str, Any]] | None) -> bool:
    """检查消息历史中是否包含来自 Operator 的消息（即人类操作员主动发言）。

    兼容两类 prompt 结构：
    - 当前 YAML 格式：`sender: OPERATOR` / `sender: 操作者`
    """
    markers = (
        "sender: OPERATOR",
        "sender: 操作者",
    )
    for msg in (messages or []):
        content = msg.get("content") or ""
        if any(marker in content for marker in markers):
            return True
    return False


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
        match = re.search(r"【房间《(?P<room>general|alice_private|public_group)》】", content)
        if match:
            return str(match.group("room"))
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
        - {"reasoning_content": "思考内容"}
        """
        # 如果已经包含完整字段，直接返回
        if "id" in response and "choices" in response:
            return response

        tool_calls = response.get("tool_calls", [])
        content = response.get("content")
        reasoning_content = response.get("reasoning_content")

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
        message = {
            "role": "assistant",
            "content": content,
            "tool_calls": tool_calls if tool_calls else None,
        }
        if reasoning_content:
            message["reasoning_content"] = reasoning_content

        return {
            "id": f"msg_{int(time.time() * 1000)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": "mock-model",
            "choices": [{
                "index": 0,
                "message": message,
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


def _to_sse_chunks(response_data: Dict[str, Any]) -> list[str]:
    """将 chat.completion 响应转换为 SSE 流格式的 chunk 列表。"""
    resp_id = response_data.get("id", f"mock-{int(time.time() * 1000)}")
    created = response_data.get("created", int(time.time()))
    model = response_data.get("model", "mock-model")
    choices = response_data.get("choices", [{}])
    choice = choices[0] if choices else {}
    message = choice.get("message", {})
    finish_reason = choice.get("finish_reason", "stop")
    tool_calls = message.get("tool_calls") or []
    content = message.get("content")
    reasoning_content = message.get("reasoning_content")

    base = {"id": resp_id, "object": "chat.completion.chunk", "created": created, "model": model}
    chunks = []

    # 首个 delta：角色
    first_delta: Dict[str, Any] = {"role": "assistant", "content": None}
    chunks.append({**base, "choices": [{"index": 0, "delta": first_delta, "finish_reason": None}]})

    # reasoning_content chunk（如有）
    if reasoning_content:
        chunks.append({**base, "choices": [{"index": 0, "delta": {"reasoning_content": reasoning_content}, "finish_reason": None}]})

    if tool_calls:
        for tc in tool_calls:
            tc_index = tc.get("index", 0) if "index" in tc else tool_calls.index(tc)
            fn = tc.get("function", {})
            # 工具名称 chunk
            chunks.append({**base, "choices": [{"index": 0, "delta": {"tool_calls": [{
                "index": tc_index,
                "id": tc.get("id", f"call_mock_{tc_index}"),
                "type": "function",
                "function": {"name": fn.get("name", ""), "arguments": ""},
            }]}, "finish_reason": None}]})
            # arguments chunk
            args = fn.get("arguments", "")
            if isinstance(args, dict):
                args = json.dumps(args, ensure_ascii=False)
            if args:
                chunks.append({**base, "choices": [{"index": 0, "delta": {"tool_calls": [{
                    "index": tc_index,
                    "function": {"arguments": args},
                }]}, "finish_reason": None}]})
    elif content:
        chunks.append({**base, "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}]})

    # 结束 chunk
    chunks.append({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
                   "usage": response_data.get("usage", {})})
    return [f"data: {json.dumps(c, ensure_ascii=False)}\n\n" for c in chunks] + ["data: [DONE]\n\n"]


class ChatCompletionsHandler(tornado.web.RequestHandler):
    """OpenAI 格式的 chat/completions 端点。"""

    async def post(self):
        await asyncio.sleep(MOCK_LLM_RESPONSE_DELAY_SEC)

        room_name = "general"
        is_stream = False
        messages = []
        try:
            body = json.loads(self.request.body)
            messages = body.get("messages", [])
            system_prompt = body.get("system_prompt", "")
            room_name = _infer_room_name(messages, system_prompt)
            is_stream = bool(body.get("stream", False))
        except Exception:
            pass

        # 从队列获取响应，队列为空时使用默认响应
        queue = self.application.response_queue
        if not queue.empty():
            response_data = await queue.get()
        else:
            with_send = _has_operator_message(messages)
            response_data = _default_openai_response(room_name, with_send=with_send)

        if is_stream:
            self.set_header("Content-Type", "text/event-stream")
            self.set_header("Cache-Control", "no-cache")
            for chunk in _to_sse_chunks(response_data):
                self.write(chunk)
                self.flush()
        else:
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

    def __init__(self, port: int = MOCK_LLM_PORT):
        self.port: int = port
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
                self._ioloop.add_callback(self._started.set)
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
