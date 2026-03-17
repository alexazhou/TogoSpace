"""所有测试用例的基类，负责统一初始化和清理所有 service 的全局状态。"""
import asyncio
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
from mock_llm_server import MockLLMServer

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../src"))
_BACKEND_READY_TIMEOUT = 20


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ServiceTestCase:
    """基础测试类：每个用例前重置所有 service 状态，用例后清理。

    类级生命周期由 setup_class / teardown_class 管理。
    方法级别初始化/清理由子类自行实现（可调用本类提供的 reset_services/cleanup_services 辅助方法）。

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
    _backend_llm_config: str = None

    mock_llm_server: MockLLMServer = None

    # ------------------------------------------------------------------
    # 类级别生命周期
    # ------------------------------------------------------------------

    @classmethod
    def setup_class(cls):
        if cls.requires_mock_llm:
            cls._start_mock_llm()
        if cls.requires_backend:
            cls._setup_pre_backend()
            cls._start_backend()
        cls._run_maybe_async(cls.async_setup_class())

    @classmethod
    def teardown_class(cls):
        cls._run_maybe_async(cls.async_teardown_class())
        if cls.requires_backend:
            cls._stop_backend()
        if cls.requires_mock_llm:
            cls._stop_mock_llm()

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

    @classmethod
    def _stop_mock_llm(cls):
        if cls.mock_llm_server is not None:
            cls.mock_llm_server.stop()
            cls.mock_llm_server = None

    @classmethod
    def _setup_pre_backend(cls):
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

        llm_json = os.path.join(config_dir, "llm.json")
        if os.path.isfile(llm_json):
            cls._backend_llm_config = llm_json

    @classmethod
    def _start_backend(cls):
        """启动后端子进程，等待 HTTP 服务就绪。"""
        port = _find_free_port()
        env = os.environ.copy()
        env["PYTHONPATH"] = _SRC_DIR

        cmd = [sys.executable, os.path.join(_SRC_DIR, "main.py"), "--port", str(port)]
        if cls._backend_config_dir:
            cmd += ["--config-dir", cls._backend_config_dir]
        if cls._backend_llm_config:
            cmd += ["--llm-config", cls._backend_llm_config]

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
            try:
                with urllib.request.urlopen(f"{base_url}/agents", timeout=1) as resp:
                    if resp.status == 200:
                        break
            except Exception:
                pass
            time.sleep(0.3)
        else:
            proc.terminate()
            proc.wait()
            raise RuntimeError(f"后端服务在 {_BACKEND_READY_TIMEOUT}s 内未就绪")

        cls._backend_proc = proc
        cls.backend_port = port
        cls.backend_base_url = base_url

    @classmethod
    def _stop_backend(cls):
        """终止后端子进程并清理类属性。"""
        if cls._backend_proc is not None:
            cls._backend_proc.terminate()
            cls._backend_proc.wait()
            cls._backend_proc = None
            cls.backend_port = None
            cls.backend_base_url = None

    @classmethod
    def reset_services(cls):
        """重置 in-process service 状态（供子类类级初始化调用）。"""
        cls._run_maybe_async(cls.areset_services())

    @classmethod
    def cleanup_services(cls):
        """清理 in-process service 状态（供子类类级清理调用）。"""
        cls._run_maybe_async(cls.acleanup_services())

    @classmethod
    async def areset_services(cls):
        """异步重置 in-process service 状态。"""
        await message_bus.startup()
        room_service.shutdown()
        await agent_service.shutdown()
        func_tool_service.shutdown()
        scheduler.shutdown()

    @classmethod
    async def acleanup_services(cls):
        """异步清理 in-process service 状态。"""
        scheduler.shutdown()
        func_tool_service.shutdown()
        await agent_service.shutdown()
        room_service.shutdown()
        message_bus.shutdown()

    @staticmethod
    def _run_maybe_async(result):
        if inspect.isawaitable(result):
            asyncio.run(result)
