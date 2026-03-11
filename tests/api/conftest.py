"""
端到端测试 fixtures：
- 启动 Mock LLM Server（Tornado）
- 以子进程启动 main.py，注入测试配置
- 等待服务就绪后 yield，teardown 时清理资源
"""
import asyncio
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import aiohttp
import pytest
import pytest_asyncio

from mock_llm_server import MockLLMServer

READY_TIMEOUT = 20  # 秒


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def mock_llm_server():
    server = MockLLMServer()
    server.start()
    yield server
    server.stop()


def _write_e2e_configs(mock_port: int, tmp_dir: str):
    """写入测试用配置文件，返回 resource_dir。"""
    resource_dir = os.path.join(tmp_dir, "resource")
    agents_dir = os.path.join(resource_dir, "agents")
    teams_dir = os.path.join(resource_dir, "teams")
    os.makedirs(agents_dir, exist_ok=True)
    os.makedirs(teams_dir, exist_ok=True)

    # Agent 定义
    alice_agent = {"name": "alice", "prompt_file": "resource/prompts/alice_system.md", "model": "mock-model"}
    with open(os.path.join(agents_dir, "alice.json"), "w", encoding="utf-8") as f:
        json.dump(alice_agent, f, ensure_ascii=False)

    # Team 定义
    team = {
        "name": "e2e",
        "groups": [
            {"name": "general", "type": "group", "members": ["alice"], "initial_topic": "e2e 测试话题", "max_turns": 50}
        ],
        "max_function_calls": 2,
    }
    with open(os.path.join(teams_dir, "e2e.json"), "w", encoding="utf-8") as f:
        json.dump(team, f, ensure_ascii=False)

    # LLM 配置
    llm_cfg = {
        "llm_services": [
            {
                "name": "mock",
                "base_url": f"http://127.0.0.1:{mock_port}/v1/chat/completions",
                "api_key": "mock-api-key",
                "type": "openai-compatible",
            }
        ],
        "active_llm_service": "mock",
    }
    llm_path = os.path.join(tmp_dir, "llm_e2e.json")
    with open(llm_path, "w", encoding="utf-8") as f:
        json.dump(llm_cfg, f)

    return resource_dir, llm_path


@pytest.fixture(scope="session")
def backend_port() -> int:
    return _find_free_port()


@pytest.fixture(scope="session")
def backend_base(backend_port) -> str:
    return f"http://127.0.0.1:{backend_port}"


@pytest.fixture(scope="session")
def backend_process(mock_llm_server, backend_port, backend_base, tmp_path_factory):
    """启动后端子进程，就绪后同步启动 WS 事件收集线程（非阻塞），然后 yield。"""
    tmp_dir = str(tmp_path_factory.mktemp("e2e_config"))
    resource_dir, llm_path = _write_e2e_configs(mock_llm_server.port, tmp_dir)

    src_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../src"))
    env = os.environ.copy()
    env["PYTHONPATH"] = src_dir

    proc = subprocess.Popen(
        [
            sys.executable,
            os.path.join(src_dir, "main.py"),
            "--resource-dir", resource_dir,
            "--llm-config", llm_path,
            "--port", str(backend_port),
        ],
        cwd=src_dir,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    # 等待服务就绪
    deadline = time.time() + READY_TIMEOUT
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{backend_base}/agents", timeout=1) as resp:
                if resp.status == 200:
                    break
        except Exception:
            pass
        time.sleep(0.3)
    else:
        proc.terminate()
        proc.wait()
        raise RuntimeError(f"后端服务在 {READY_TIMEOUT}s 内未就绪")

    yield proc

    proc.terminate()
    proc.wait()


@pytest.fixture(scope="session")
def ws_events(backend_process, backend_port) -> list:
    """连接 WebSocket 并等待至少一条事件（最多 20s），在独立线程运行。"""
    collected: list = []
    ws_done = threading.Event()

    async def _collect_ws():
        ws_url = f"ws://127.0.0.1:{backend_port}/ws/events"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(ws_url) as ws:
                    try:
                        async with asyncio.timeout(18):
                            async for msg in ws:
                                if msg.type == aiohttp.WSMsgType.TEXT:
                                    data = json.loads(msg.data)
                                    if data.get("event") == "message":
                                        collected.append(data)
                                        break
                                elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                                    break
                    except asyncio.TimeoutError:
                        pass
        except Exception:
            pass
        finally:
            ws_done.set()

    def _ws_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_collect_ws())
        loop.close()

    ws_thread = threading.Thread(target=_ws_thread, daemon=True)
    ws_thread.start()
    ws_done.wait(timeout=22)

    return collected


@pytest_asyncio.fixture
async def http_client():
    async with aiohttp.ClientSession() as session:
        yield session
