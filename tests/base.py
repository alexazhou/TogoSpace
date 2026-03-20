"""所有测试用例的基类，负责统一初始化和清理所有 service 的全局状态。"""
import asyncio
import contextlib
import inspect
import os
import socket
import subprocess
import sys
import time
import urllib.request

import service.message_bus as message_bus
import service.room_service as room_service
import service.agent_service as agent_service
import service.func_tool_service as func_tool_service
import service.scheduler_service as scheduler
import service.persistence_service as persistence_service
import service.orm_service as orm_service
from mock_llm_server import MockLLMServer

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


class ServiceTestCase:
    """基础测试类：统一管理测试类级别的初始化与清理。

    类级生命周期由 setup_class / teardown_class 管理。
    推荐在 async_setup_class / async_teardown_class 中调用
    areset_services / acleanup_services 做进程内状态隔离。

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

    # ------------------------------------------------------------------
    # 类级别生命周期
    # ------------------------------------------------------------------

    @classmethod
    def setup_class(cls):
        # 先启动外部依赖（MockLLM/后端子进程），再执行子类自定义异步初始化。
        try:
            if cls.requires_mock_llm:
                cls._start_mock_llm()
            if cls.requires_backend:
                cls._setup_config()
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
            f"http://127.0.0.1:{cls.mock_llm_server.port}/v1/chat/completions",
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
    def _setup_config(cls):
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
    def _start_backend(cls):
        """启动后端子进程，等待 HTTP 服务就绪。"""
        port = _find_free_port()
        env = os.environ.copy()
        env["PYTHONPATH"] = _SRC_DIR

        cmd = [sys.executable, os.path.join(_SRC_DIR, "main.py"), "--port", str(port)]
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
                _assert_port_ready(f"{base_url}/agents", "后端", timeout=0.5)
                break
            except RuntimeError:
                pass
            time.sleep(0.3)
        else:
            with contextlib.suppress(Exception):
                proc.terminate()
            with contextlib.suppress(Exception):
                proc.wait(timeout=5)
            output = cls._tail_text(cls._read_process_output(proc))
            raise RuntimeError(f"后端服务在 {_BACKEND_READY_TIMEOUT}s 内未就绪\n{output}")

        cls._backend_proc = proc
        cls.backend_port = port
        cls.backend_base_url = base_url
        _assert_port_ready(f"{cls.backend_base_url}/agents", "后端")

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

    @classmethod
    def reset_services(cls):
        """同步壳：在非异步 setup_class 中安全调用异步重置逻辑。"""
        cls._run_maybe_async(cls.areset_services())

    @classmethod
    def cleanup_services(cls):
        """同步壳：在非异步 teardown_class 中安全调用异步清理逻辑。"""
        cls._run_maybe_async(cls.acleanup_services())

    @classmethod
    async def areset_services(cls):
        """异步重置 in-process service 状态。"""
        await message_bus.startup()
        room_service.shutdown()
        await agent_service.shutdown()
        await persistence_service.shutdown()
        await orm_service.shutdown()
        func_tool_service.shutdown()
        scheduler.shutdown()

    @classmethod
    async def acleanup_services(cls):
        """异步清理 in-process service 状态。"""
        scheduler.shutdown()
        func_tool_service.shutdown()
        await persistence_service.shutdown()
        await orm_service.shutdown()
        await agent_service.shutdown()
        room_service.shutdown()
        message_bus.shutdown()

    @staticmethod
    def _run_maybe_async(result):
        # pytest 的 setup_class/teardown_class 是同步协议，这里统一桥接 awaitable。
        if inspect.isawaitable(result):
            asyncio.run(result)
