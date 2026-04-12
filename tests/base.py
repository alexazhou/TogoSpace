"""所有测试用例的基类，负责统一初始化和清理所有 service 的全局状态。"""
import asyncio
import contextlib
import hashlib
import inspect
import json
import os
import socket
import subprocess
import sys
import time
import unittest.mock as mock
import urllib.request

import db as db_tool
import service.messageBus as messageBus
import service.roomService as roomService
import service.agentService as agentService
import service.funcToolService as funcToolService
import service.schedulerService as scheduler
import service.persistenceService as persistenceService
import service.ormService as ormService
import service.llmService as llmService
from dal.db import gtAgentManager, gtTeamManager, gtRoomManager
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtRoom import GtRoom
from util.configTypes import AgentConfig
from util import configUtil
from util.llmApiUtil import OpenAIMessage, OpenAIToolCall
from mock_llm_server import (
    MockLLMServer,
    MOCK_LLM_HOST,
    get_mock_llm_api_url,
    get_mock_llm_anthropic_url,
)
from constants import OpenaiApiRole, EmployStatus, RoomType, SpecialAgent

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../src"))
_BACKEND_READY_TIMEOUT = 20
_BASE_BACKEND_PORT = 18080
_BASE_MOCK_LLM_PORT = 19876

# 禁止系统代理（如 Surge）拦截本地回环流量，防止旧连接复用导致健康检查和 LLM 调用挂起。
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
os.environ.setdefault("no_proxy", "127.0.0.1,localhost")


def _cancel_all_tasks(loop: asyncio.AbstractEventLoop) -> None:
    """取消事件循环上的所有待处理 task（与 asyncio.run 的清理逻辑一致）。"""
    tasks = [t for t in asyncio.all_tasks(loop) if not t.done()]
    if not tasks:
        return
    for task in tasks:
        task.cancel()
    loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))


def _get_worker_offset() -> int:
    worker_id = os.environ.get("PYTEST_XDIST_WORKER")
    if worker_id and worker_id.startswith("gw"):
        try:
            return int(worker_id[2:])
        except ValueError:
            return 0
    return 0


def _get_backend_port() -> int:
    return _BASE_BACKEND_PORT + _get_worker_offset()


def _get_mock_llm_port() -> int:
    return _BASE_MOCK_LLM_PORT + _get_worker_offset()


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _no_proxy_urlopen(request: urllib.request.Request, timeout: float):
    """不使用系统代理打开 URL，避免 Surge 等代理工具干扰本地 HTTP 健康检查。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(request, timeout=timeout)


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
        with _no_proxy_urlopen(request, timeout=timeout) as resp:
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


def _wait_port_released(host: str, port: int, timeout: float = 3.0) -> None:
    """等待端口释放（用于 teardown 后确保端口可用）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind((host, port))
                # 绑定成功说明端口已释放
                return
        except OSError:
            time.sleep(0.1)
    # 超时不报错，让下一个测试自己处理


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

    @classmethod
    def _get_test_db_path(cls) -> str:
        worker_id = os.environ.get("PYTEST_XDIST_WORKER")
        class_key = f"{cls.__module__}.{cls.__name__}"
        class_hash = hashlib.md5(class_key.encode("utf-8")).hexdigest()[:10]
        if worker_id:
            return f"/tmp/teamagent_tests_{worker_id}_{class_hash}.db"
        return f"/tmp/teamagent_tests_{class_hash}.db"

    @property
    def test_db_path(self) -> str:
        return self._get_test_db_path()

    # ------------------------------------------------------------------
    # LLM Patching (In-Process Mocking)
    # ------------------------------------------------------------------

    @contextlib.contextmanager
    def patch_infer(self, responses: list[dict] = None, handler=None):
        """统一封装对 llmService.infer / infer_stream 的 Mock 注入。

        同时 patch infer 和 infer_stream，使 compact（调用 infer）与
        agentTurnRunner（调用 infer_stream）共享同一个 mock 队列。
        
        用法 (简化字典):
            with self.patch_infer(responses=[{"content": "你好"}]):
                await ...

        用法 (工具调用):
            with self.patch_infer(responses=[{
                "tool_calls": [{"name": "send_chat_msg", "arguments": {"msg": "hi"}}]
            }]):
                await ...
        """
        target = "service.agentService.core.llmService.infer"
        target_stream = "service.agentService.core.llmService.infer_stream"

        async def _to_infer_result(value):
            if isinstance(value, llmService.InferResult):
                return value
            return llmService.InferResult.success(value)

        if responses is not None:
            # 将简化字典序列转换为 Mock 对象序列
            mock_responses = [llmService.InferResult.success(self.normalize_to_mock(r)) for r in responses]
            m = mock.AsyncMock(side_effect=mock_responses)
            with mock.patch(target, m), mock.patch(target_stream, m) as p:
                yield p
        elif handler is not None:
            async def _wrapped_handler(*args, **kwargs):
                kwargs.pop("on_progress", None)
                try:
                    value = await handler(*args, **kwargs)
                except Exception as e:
                    return llmService.InferResult.failure(e)
                return await _to_infer_result(value)

            with mock.patch(target, side_effect=_wrapped_handler), \
                 mock.patch(target_stream, side_effect=_wrapped_handler) as p:
                yield p
        else:
            default = mock.AsyncMock(return_value=llmService.InferResult.success(self.normalize_to_mock({"content": "ok"})))
            with mock.patch(target, default), mock.patch(target_stream, default) as p:
                yield p

    def normalize_to_mock(self, data: dict):
        """将简化格式的响应字典转换为完整的 Mock 响应对象。"""
        if isinstance(data, (mock.MagicMock, mock.AsyncMock)):
            return data

        content = data.get("content")
        tool_calls_raw = data.get("tool_calls", [])
        tool_calls = []

        for tc in tool_calls_raw:
            args = tc.get("arguments", {})
            if isinstance(args, dict):
                args = json.dumps(args, ensure_ascii=False)
            tool_calls.append(OpenAIToolCall(
                id=tc.get("id", f"call_{int(time.time() * 1000)}"),
                function={"name": tc["name"], "arguments": args}
            ))

        msg = OpenAIMessage(
            role=OpenaiApiRole.ASSISTANT,
            content=content,
            tool_calls=tool_calls if tool_calls else None
        )

        # 模拟结构: resp.choices[0].message, resp.usage
        mock_resp = mock.MagicMock()
        mock_choice = mock.MagicMock()
        mock_choice.message = msg
        mock_resp.choices = [mock_choice]
        mock_resp.usage = None
        return mock_resp

    # ------------------------------------------------------------------
    # 类级别生命周期
    # ------------------------------------------------------------------

    _class_loop: asyncio.AbstractEventLoop = None

    @classmethod
    def setup_class(cls):
        # 创建类级别事件循环，确保 setup 与 teardown 共用同一个 loop，
        # 避免 TSP 等驱动的 asyncio 资源跨循环后无法正常关闭。
        cls._class_loop = asyncio.new_event_loop()
        try:
            cls._load_config()
            cls.cleanup_sqlite_files()
            cls.prepare_sqlite_schema()
            if cls.requires_mock_llm:
                cls._start_mock_llm()
            if cls.requires_backend:
                cls._start_backend()
            cls._run_on_class_loop(cls.async_setup_class())
        except Exception:
            cls._safe_cleanup_external_dependencies()
            cls._close_class_loop()
            raise

    @classmethod
    def teardown_class(cls):
        # 先执行子类清理，再关闭外部依赖，保证清理阶段仍可访问服务。
        teardown_error: Exception | None = None
        try:
            cls._run_on_class_loop(cls.async_teardown_class())
        except Exception as exc:
            teardown_error = exc
        finally:
            if hasattr(cls, "_config_patcher"):
                cls._config_patcher.stop()
            cls._safe_cleanup_external_dependencies()
            cls._close_class_loop()
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
        port = _get_mock_llm_port()
        _wait_port_released(MOCK_LLM_HOST, port)
        cls.mock_llm_server = MockLLMServer(port=port)
        cls.mock_llm_server.start()
        _assert_port_ready(
            get_mock_llm_api_url(port=port),
            "MockLLM",
            timeout=10.0,
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
        with _no_proxy_urlopen(req, timeout=2) as resp:
            if resp.status != 200:
                raise RuntimeError(f"设置 Mock LLM 响应失败: {resp.status}")

    @classmethod
    def get_mock_response(cls) -> dict | None:
        """从 Mock LLM Server 响应队列获取下一个响应。

        Returns:
            响应字典，队列为空时返回 None
        """
        url = f"http://{MOCK_LLM_HOST}:{cls.mock_llm_server.port}/get_response"
        req = urllib.request.Request(url, method="GET")
        with _no_proxy_urlopen(req, timeout=2) as resp:
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

        # 强制重写配置中的 DB 路径为 worker 专属路径
        db_path = cls._get_test_db_path()
        original_load = configUtil.load

        def patched_load(path=None, preset_dir=None, force_reload=False):
            cfg = original_load(path or cls._backend_config_dir, preset_dir=preset_dir or cls._backend_config_dir, force_reload=force_reload)
            if cfg.setting.persistence:
                cfg.setting.persistence.db_path = db_path
            if cls.requires_mock_llm:
                mock_port = _get_mock_llm_port()
                for service in cfg.setting.llm_services:
                    if service.type.name.lower() == "anthropic":
                        service.base_url = get_mock_llm_anthropic_url(port=mock_port)
                    else:
                        service.base_url = get_mock_llm_api_url(port=mock_port)
            return cfg

        cls._config_patcher = mock.patch("util.configUtil.load", side_effect=patched_load)
        cls._config_patcher.start()

    @classmethod
    def cleanup_sqlite_files(cls) -> None:
        """删除测试 DB 文件（含后端子进程使用的 DB）。"""
        paths = [cls._get_test_db_path()]
        setting = configUtil.load(cls._backend_config_dir).setting
        path = setting.persistence.db_path
        if path:
            paths.append(path if os.path.isabs(path) else os.path.abspath(os.path.join(_SRC_DIR, path)))
        for p in paths:
            with contextlib.suppress(FileNotFoundError):
                os.remove(p)

    @classmethod
    def prepare_sqlite_schema(cls) -> None:
        """为测试预创建数据库 schema，避免依赖 ormService 启动时自动建表。"""
        paths = [cls._get_test_db_path()]
        setting = configUtil.load(cls._backend_config_dir).setting
        path = setting.persistence.db_path
        if path:
            paths.append(path if os.path.isabs(path) else os.path.abspath(os.path.join(_SRC_DIR, path)))

        for p in dict.fromkeys(paths):
            db_tool.migrate_database(p)

    @classmethod
    def _start_backend(cls):
        """启动后端子进程，等待 HTTP 服务就绪。"""
        port = _get_backend_port()
        _wait_port_released("127.0.0.1", port)

        env = os.environ.copy()
        env["PYTHONPATH"] = _SRC_DIR
        env["TEAMAGENT_ENV"] = "test"
        env["TEAMAGENT_DB_PATH"] = cls._get_test_db_path()

        if cls._backend_config_dir:
            env["TEAMAGENT_PRESET_DIR"] = cls._backend_config_dir

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
            time.sleep(0.05)
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
        if hasattr(cls, "_config_patcher"):
            with contextlib.suppress(Exception):
                cls._config_patcher.stop()
            with contextlib.suppress(Exception):
                delattr(cls, "_config_patcher")
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
    def _run_on_class_loop(cls, coro):
        """在类级别事件循环上运行协程，保证 setup/teardown 共用同一 loop。"""
        if not inspect.isawaitable(coro):
            return
        loop = cls._class_loop
        if loop is None or loop.is_closed():
            asyncio.run(coro)
            return
        loop.run_until_complete(coro)

    @classmethod
    def _close_class_loop(cls):
        """关闭类级别事件循环。"""
        loop = cls._class_loop
        cls._class_loop = None
        if loop is not None and not loop.is_closed():
            try:
                _cancel_all_tasks(loop)
                loop.run_until_complete(loop.shutdown_asyncgens())
            finally:
                loop.close()

    @classmethod
    def _run_maybe_async(cls, result):
        """同步桥接 awaitable：优先使用类级别事件循环，否则回退到 asyncio.run。"""
        if not inspect.isawaitable(result):
            return
        loop = getattr(cls, "_class_loop", None)
        if loop is not None and not loop.is_closed():
            loop.run_until_complete(result)
        else:
            asyncio.run(result)

    @staticmethod
    async def wait_until(
        predicate,
        timeout: float = 2.0,
        interval: float = 0.05,
        message: str = "等待条件成立超时",
    ) -> None:
        """轮询等待条件成立，避免在测试里写固定长 sleep。"""
        deadline = time.monotonic() + timeout
        while True:
            if predicate():
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(message)
            await asyncio.sleep(min(interval, remaining))

    @staticmethod
    async def create_room(
        team_name: str,
        room_name: str,
        agent_names: list[str],
        initial_topic: str = "",
        room_type: RoomType = RoomType.GROUP,
        max_turns: int = 0,
    ) -> None:
        """测试辅助：通过生产 API（gtRoomManager.save_room + load_all_rooms）创建或更新房间。"""
        gt_team = await gtTeamManager.get_team(team_name)
        assert gt_team is not None, f"Team '{team_name}' 不存在"
        agents = await gtAgentManager.get_team_agents_by_names(gt_team.id, agent_names, include_special=True)
        agent_ids = [a.id for a in agents]
        gt_room = await gtRoomManager.get_room_by_team_and_name(gt_team.id, room_name)
        if gt_room is None:
            gt_room = GtRoom(
                team_id=gt_team.id,
                name=room_name,
                type=room_type,
                initial_topic=initial_topic,
                max_turns=max_turns,
                agent_ids=agent_ids,
                biz_id=None,
                tags=[],
            )
        else:
            gt_room.type = room_type
            gt_room.initial_topic = initial_topic
            gt_room.max_turns = max_turns
            gt_room.agent_ids = agent_ids
        await gtRoomManager.save_room(gt_room)
        await roomService.load_all_rooms()

    @staticmethod
    async def convert_to_gt_agents(team_id: int, configs: list[AgentConfig]) -> list[GtAgent]:
        """测试辅助：将 AgentConfig 列表转换为 GtAgent 列表（包含角色模板解析）。"""
        agents = []
        for cfg in configs:
            rt_id = await gtAgentManager.resolve_role_template_id_by_name(cfg.role_template)
            agents.append(GtAgent(
                team_id=team_id,
                name=cfg.name,
                role_template_id=rt_id,
                model=cfg.model or "",
                driver=cfg.driver,
                employ_status=EmployStatus.ON_BOARD,
            ))
        return agents

    @staticmethod
    async def convert_to_gt_rooms(team_id: int, configs: list) -> list[GtRoom]:
        def infer_room_type(agent_names: list[str]) -> RoomType:
            ai_count = len([agent_name for agent_name in agent_names if SpecialAgent.value_of(agent_name) != SpecialAgent.OPERATOR])
            if any(SpecialAgent.value_of(agent_name) == SpecialAgent.OPERATOR for agent_name in agent_names) and ai_count == 1:
                return RoomType.PRIVATE
            return RoomType.GROUP

        rooms = []
        for cfg in configs:
            agent_names = list(cfg.agents)
            agent_ids = [
                agent.id
                for agent in await gtAgentManager.get_team_agents_by_names(
                    team_id,
                    agent_names,
                    include_special=True,
                )
            ]
            rooms.append(GtRoom(
                id=getattr(cfg, "id", None),
                team_id=team_id,
                name=cfg.name,
                type=infer_room_type(agent_names),
                initial_topic=cfg.initial_topic,
                max_turns=roomService.resolve_room_max_turns(cfg.max_turns),
                agent_ids=agent_ids,
                biz_id=getattr(cfg, "biz_id", None),
                tags=list(getattr(cfg, "tags", [])),
            ))
        return rooms
