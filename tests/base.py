"""所有测试用例的基类，负责统一初始化和清理所有 service 的全局状态。"""
import asyncio
import contextlib
import inspect
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

import service.messageBus as messageBus
import service.roomService as roomService
import service.agentService as agentService
import service.funcToolService as funcToolService
import service.schedulerService as scheduler
import service.persistenceService as persistenceService
import service.ormService as ormService
from util import configUtil
from mock_llm_server import (
    MockLLMServer,
    MOCK_LLM_HOST,
    get_mock_llm_api_url,
)

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../src"))
_BACKEND_READY_TIMEOUT = 20


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _assert_port_ready(
    url: str,
    service_name: str,
    timeout: float = 1.0,
    method: str = "GET",
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> None:
    """就绪定义：请求 HTTP URL 且返回 200。"""
    try:
        request = urllib.request.Request(
            url,
            data=data,
            headers=headers or {},
            method=method,
        )
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            if resp.status != 200:
                raise RuntimeError(
                    f"{service_name} 健康检查失败：{method} {url} => {resp.status}"
                )
    except Exception as exc:
        raise RuntimeError(
            f"{service_name} 健康检查失败：{method} {url} => {exc}"
        ) from exc


def _assert_tcp_ready(host: str, port: int, service_name: str, timeout: float = 1.0) -> None:
    deadline = time.time() + timeout
    last_exc: Exception | None = None
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.3):
                return
        except Exception as exc:
            last_exc = exc
            time.sleep(0.05)
    raise RuntimeError(f"{service_name} TCP 健康检查失败：{host}:{port} => {last_exc or 'timeout'}")


class ServiceTestCase:
    """基础测试类：统一管理测试类级别的初始化与清理。

    类级生命周期由 setup_class / teardown_class 管理。
    子类按需在 async_setup_class / async_teardown_class 中初始化 service。

    后端子进程支持：
        requires_backend = True   — 在整个测试类前后自动启动/停止后端子进程
        requires_mock_llm = True  — 同时自动启动/停止 MockLLMServer

    配置目录选择：
        use_custom_config = True  — 使用测试类自己的 config/ 目录
        use_custom_config = False — 使用 tests/config/ 默认配置目录

    启动完成后可通过 self.backend_base_url / self.backend_port 访问服务地址。
    """

    requires_backend: bool = False
    requires_mock_llm: bool = False
    use_custom_config: bool = False

    backend_port: int = None
    backend_base_url: str = None
    _backend_proc: subprocess.Popen = None
    _backend_config_dir: str = None

    mock_llm_server: MockLLMServer = None
    TEST_DB_PATH: str = "/tmp/teamagent_tests.db"

    # ------------------------------------------------------------------
    # LLM Patching (In-Process Mocking)
    # ------------------------------------------------------------------

    @contextlib.contextmanager
    def patch_infer(self, responses: list[dict] = None, handler=None):
        """统一封装对 llmService.infer 的 Mock 注入。
        
        用法 (简化字典):
            with self.patch_infer(responses=[{"content": "你好"}]):
                await ...

        用法 (工具调用):
            with self.patch_infer(responses=[{
                "tool_calls": [{"name": "send_chat_msg", "arguments": {"msg": "hi"}}]
            }]):
                await ...
        """
        import unittest.mock as mock
        target = "service.agentService.llmService.infer"
        
        if responses is not None:
            # 将简化字典序列转换为 Mock 对象序列
            mock_responses = [self.normalize_to_mock(r) for r in responses]
            m = mock.AsyncMock(side_effect=mock_responses)
            with mock.patch(target, m) as p:
                yield p
        elif handler is not None:
            with mock.patch(target, side_effect=handler) as p:
                yield p
        else:
            with mock.patch(target, new_callable=mock.AsyncMock) as p:
                yield p

    def normalize_to_mock(self, data: dict):
        """将简化格式的响应字典转换为完整的 Mock 响应对象。"""
        import unittest.mock as mock
        from util.llmApiUtil import LlmApiMessage, ToolCall
        from constants import OpenaiLLMApiRole
        import json

        if isinstance(data, (mock.MagicMock, mock.AsyncMock)):
            return data

        content = data.get("content")
        tool_calls_raw = data.get("tool_calls", [])
        tool_calls = []

        for tc in tool_calls_raw:
            args = tc.get("arguments", {})
            if isinstance(args, dict):
                args = json.dumps(args, ensure_ascii=False)
            tool_calls.append(ToolCall(
                id=tc.get("id", f"call_{int(time.time() * 1000)}"),
                function={"name": tc["name"], "arguments": args}
            ))

        msg = LlmApiMessage(
            role=OpenaiLLMApiRole.ASSISTANT,
            content=content,
            tool_calls=tool_calls if tool_calls else None
        )

        # 模拟结构: resp.choices[0].message
        mock_resp = mock.MagicMock()
        mock_choice = mock.MagicMock()
        mock_choice.message = msg
        mock_resp.choices = [mock_choice]
        return mock_resp

    # ------------------------------------------------------------------
    # 类级别生命周期
    # ------------------------------------------------------------------

    @classmethod
    def setup_class(cls):
        # 先启动外部依赖（MockLLM/后端子进程），再执行子类自定义异步初始化。
        try:
            if cls.requires_backend:
                cls._load_config()
            cls.cleanup_sqlite_files()
            if cls.requires_mock_llm:
                cls._start_mock_llm()
            if cls.requires_backend:
                cls._start_backend()
            cls._run_maybe_async(cls.async_setup_class())
        except Exception:
            cls._safe_cleanup_external_dependencies()
            raise

    @classmethod
    def teardown_class(cls):
        # 先执行子类清理，再关闭外部依赖，保证清理阶段仍可访问服务。
        teardown_error: Exception | None = None
        try:
            cls._run_maybe_async(cls.async_teardown_class())
        except Exception as exc:
            teardown_error = exc
        finally:
            cls._safe_cleanup_external_dependencies()
            cls.cleanup_sqlite_files()
        if teardown_error is not None:
            raise teardown_error

    @classmethod
    async def async_setup_class(cls):
        """子类可按需重写：类级别异步初始化。"""

    @classmethod
    async def async_teardown_class(cls):
        """子类可按需重写：类级别异步清理。"""

    @classmethod
    def _start_mock_llm(cls):
        cls.mock_llm_server = MockLLMServer()
        cls.mock_llm_server.start()
        _assert_port_ready(
            get_mock_llm_api_url(),
            "MockLLM",
            method="POST",
            data=b"{}",
            headers={"Content-Type": "application/json"},
        )

    @classmethod
    def _stop_mock_llm(cls):
        if cls.mock_llm_server is not None:
            cls.mock_llm_server.stop()
            cls.mock_llm_server = None

    @classmethod
    def set_mock_response(cls, response: dict) -> None:
        """设置 Mock LLM Server 的响应，推入队列。

        Args:
            response: 响应内容，支持：
                - 简化格式：{"tool_calls": [{"name": "xxx", "arguments": "..."}]}
                - 简化格式：{"content": "..."}
                - 完整格式：{"choices": [{"message": {...}}]}
        """
        url = f"http://{MOCK_LLM_HOST}:{cls.mock_llm_server.port}/set_response"
        req = urllib.request.Request(
            url,
            data=json.dumps({"response": response}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            if resp.status != 200:
                raise RuntimeError(f"设置 Mock LLM 响应失败: {resp.status}")

    @classmethod
    def get_mock_response(cls) -> dict | None:
        """从 Mock LLM Server 响应队列获取下一个响应。

        Returns:
            响应字典，队列为空时返回 None
        """
        url = f"http://{MOCK_LLM_HOST}:{cls.mock_llm_server.port}/get_response"
        with urllib.request.urlopen(url, timeout=2) as resp:
            if resp.status != 200:
                raise RuntimeError(f"获取 Mock LLM 响应失败: {resp.status}")
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("response")

    @classmethod
    def _load_config(cls):
        """配置选择机制：
        - 若 use_custom_config = True，使用测试类自己的 config/ 目录
        - 否则使用 tests/config/ 默认配置目录
        """
        # 确定使用的配置目录
        if cls.use_custom_config:
            test_file = sys.modules[cls.__module__].__file__
            test_dir = os.path.dirname(os.path.abspath(test_file))
            config_dir = os.path.join(test_dir, "config")
        else:
            config_dir = os.path.join(os.path.dirname(__file__), "config")

        if not os.path.isdir(config_dir):
            return

        cls._backend_config_dir = config_dir

    @classmethod
    def cleanup_sqlite_files(cls) -> None:
        """删除测试 DB 文件（含后端子进程使用的 DB）。"""
        paths = [cls.TEST_DB_PATH]
        persistence_cfg = configUtil.load_persistence_config(cls._backend_config_dir)
        path = persistence_cfg.get("db_path")
        if path:
            paths.append(path if os.path.isabs(path) else os.path.abspath(os.path.join(_SRC_DIR, path)))
        for p in paths:
            with contextlib.suppress(FileNotFoundError):
                os.remove(p)

    @classmethod
    def _start_backend(cls):
        """启动后端子进程，等待 HTTP 服务就绪。"""
        port = _find_free_port()
        env = os.environ.copy()
        env["PYTHONPATH"] = _SRC_DIR
        env["TEAMAGENT_ENV"] = "test"

        cmd = [sys.executable, os.path.join(_SRC_DIR, "backend_main.py"), "--port", str(port)]
        if cls._backend_config_dir:
            cmd += ["--config-dir", cls._backend_config_dir]

        proc = subprocess.Popen(
            cmd,
            cwd=_SRC_DIR,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        base_url = f"http://127.0.0.1:{port}"
        deadline = time.time() + _BACKEND_READY_TIMEOUT
        while time.time() < deadline:
            if proc.poll() is not None:
                output = cls._tail_text(cls._read_process_output(proc))
                raise RuntimeError(
                    f"后端进程提前退出（code={proc.returncode}）\n{output}"
                )
            try:
                _assert_tcp_ready("127.0.0.1", port, "后端", timeout=0.3)
                break
            except RuntimeError:
                pass
            time.sleep(0.3)
        else:
            with contextlib.suppress(Exception):
                proc.terminate()
            with contextlib.suppress(Exception):
                proc.wait(timeout=5)
            with contextlib.suppress(Exception):
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=2)
            output = cls._tail_text(cls._read_process_output(proc))
            raise RuntimeError(f"后端服务在 {_BACKEND_READY_TIMEOUT}s 内未就绪\n{output}")

        cls._backend_proc = proc
        cls.backend_port = port
        cls.backend_base_url = base_url
        _assert_tcp_ready("127.0.0.1", cls.backend_port, "后端", timeout=1.0)

    @classmethod
    def _stop_backend(cls):
        """终止后端子进程并清理类属性。"""
        if cls._backend_proc is not None:
            with contextlib.suppress(Exception):
                if cls._backend_proc.poll() is None:
                    cls._backend_proc.terminate()
                    cls._backend_proc.wait(timeout=5)
                else:
                    cls._backend_proc.wait(timeout=1)
            with contextlib.suppress(Exception):
                if cls._backend_proc.poll() is None:
                    cls._backend_proc.kill()
                    cls._backend_proc.wait(timeout=2)
            cls._backend_proc = None
            cls.backend_port = None
            cls.backend_base_url = None
            cls._backend_config_dir = None

    @classmethod
    def _safe_cleanup_external_dependencies(cls):
        """尽最大努力清理外部依赖；用于 setup/teardown 的 finally 路径。"""
        if cls.requires_backend:
            with contextlib.suppress(Exception):
                cls._stop_backend()
        if cls.requires_mock_llm:
            with contextlib.suppress(Exception):
                cls._stop_mock_llm()

    @staticmethod
    def _read_process_output(proc: subprocess.Popen) -> str:
        if proc.stdout is None:
            return ""
        try:
            out = proc.stdout.read()
        except Exception:
            return ""
        if isinstance(out, bytes):
            return out.decode("utf-8", errors="replace")
        return out or ""

    @staticmethod
    def _tail_text(text: str, max_lines: int = 30) -> str:
        if not text:
            return "(无输出)"
        lines = text.strip().splitlines()
        return "\n".join(lines[-max_lines:])

    @staticmethod
    def _run_maybe_async(result):
        # pytest 的 setup_class/teardown_class 是同步协议，这里统一桥接 awaitable。
        if inspect.isawaitable(result):
            asyncio.run(result)
