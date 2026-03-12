"""所有测试用例的基类，负责统一初始化和清理所有 service 的全局状态。"""
import json
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

    使用 pytest 的 setup_method / teardown_method 钩子（对应 unittest 的 setUp / tearDown）。
    子类可重写这两个方法，但须在首行调用 super()。

    后端子进程支持：
        requires_backend = True   — 在整个测试类前后自动启动/停止后端子进程
        requires_mock_llm = True  — 同时自动启动/停止 MockLLMServer

    子类可重写 _setup_pre_backend() 钩子，在后端启动前创建自定义配置文件并设置：
        cls._backend_config_dir  — 传给 --config-dir 参数
        cls._backend_llm_config  — 传给 --llm-config 参数（requires_mock_llm=True 时
                                    可通过 cls.mock_llm_port 获取 mock 地址）

    启动完成后可通过 self.backend_base_url / self.backend_port 访问服务地址。
    """

    requires_backend: bool = False
    requires_mock_llm: bool = False

    backend_port: int = None
    backend_base_url: str = None
    _backend_proc: subprocess.Popen = None
    _backend_config_dir: str = None
    _backend_llm_config: str = None

    mock_llm_server: MockLLMServer = None
    mock_llm_port: int = None

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

    @classmethod
    def teardown_class(cls):
        if cls.requires_backend:
            cls._stop_backend()
        if cls.requires_mock_llm:
            cls._stop_mock_llm()

    @classmethod
    def _start_mock_llm(cls):
        """启动 MockLLMServer，并将端口暴露为 cls.mock_llm_port。"""
        cls.mock_llm_server = MockLLMServer()
        cls.mock_llm_server.start()
        cls.mock_llm_port = cls.mock_llm_server.port

    @classmethod
    def _stop_mock_llm(cls):
        if cls.mock_llm_server is not None:
            cls.mock_llm_server.stop()
            cls.mock_llm_server = None
            cls.mock_llm_port = None

    @classmethod
    def _setup_pre_backend(cls):
        """子类重写此方法，在后端启动前配置 _backend_config_dir 和 _backend_llm_config。"""
        pass

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

    # ------------------------------------------------------------------
    # 方法级别生命周期（in-process service 状态）
    # ------------------------------------------------------------------

    def setup_method(self):
        message_bus.init()
        room_service.close_all()
        agent_service.close()
        func_tool_service.close()
        scheduler.stop()

    def teardown_method(self):
        scheduler.stop()
        func_tool_service.close()
        agent_service.close()
        room_service.close_all()
        message_bus.stop()
