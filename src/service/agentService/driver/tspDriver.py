from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Optional

from pytspclient import TSPClient, TSPException, ToolResult, ToolCall
from pytspclient.types import TSP_ERROR_STDOUT_CLOSED, TSP_ERROR_CONNECTION_CLOSED

import appPaths
from constants import CONTAINER_ENTER_TIMEOUT_SEC, ExecEnv, MessageBusTopic
from model.coreModel import ContainerTarget, ExecEnvSnapshot
from service.containerService import core as containerService
from service.containerService import core as container_cli
from service.agentService.driver.base import AgentDriverConfig

from service import funcToolService, roomService, messageBus
from service.agentService import toolRegistry
from model.dbModel.gtScheculeTask import GtScheculeTask
from util import llmApiUtil

from .base import AgentDriver, AgentTurnSetup

logger = logging.getLogger(__name__)
_DEFAULT_REQUEST_TIMEOUT_SEC = 190
_RUN_CHAT_TURN_MAX_RETRIES = 3
_RUN_CHAT_TURN_HINT = (
    "你必须通过调用工具来行动。如果你不需要发言，或者已经完成了所有行动，"
    "请务必调用 finish_action 结束行动（即跳过）。"
)
_RUN_CHAT_TURN_ERROR_ACTION_HINT = (
    "系统提示: 检测到你将工具调用以 JSON 格式写入了消息文本，这将无法被送达用户。"
    "你必须通过调用 send_chat_msg / finish_action 工具，而不是在消息内容中输出 JSON。"
    "请重新行动，直接调用相应的工具。"
)


# ---------------------------------------------------------------------------
# 「容器里没有 bash」的识别与包装（step1_product.md:45 / :119，缺口 G1）
# ---------------------------------------------------------------------------

# 真实报文（alpine:3.20 与 gcr.io/distroless/base-debian12 实测**逐字相同**）：
#   {"code": "server/internal_error",
#    "message": "failed to start command: exec: \"bash\": executable file not found in $PATH"}
#
# 为什么必须用完整三要素而**不能**只匹配 `not found`：
#   bash 存在、命令不存在时（ubuntu:24.04 实测），返回的是**另一条路径**的成功调用：
#   {"success": false, "stdout": "", "stderr": "bash: line 1: xxx: command not found\n",
#    "exit_code": 127}
#   它压根不是 `TSPException`，走不到这里；但用 `not found` 这种宽串做匹配，一旦
#   将来 gtsp 把 exec 失败也塞进 stderr（或有人把本函数挪到通用异常分支），
#   就会把「用户命令写错」误报成「镜像缺 bash」—— 那会让模型去换镜像，
#   而真正该做的是改命令。所以三个片段必须同时命中：`exec:` / `"bash":` /
#   `executable file not found in $path`（比较前两边统一转小写）。
_MISSING_BASH_MARKERS = (
    'exec: "bash": executable file not found in $path',
    "exec: bash: executable file not found in $path",
)

_MISSING_BASH_MESSAGE = (
    "容器内没有 bash，execute_bash 依赖 `bash -c` 执行命令，因此该工具不可用。"
    "原因：当前镜像（如 alpine、distroless）默认不含 bash；这与 libc 无关，"
    "容器本身、文件类工具均正常。"
    "出路（二选一）：① 换自带 bash 的镜像，如 ubuntu:24.04 / debian:bookworm-slim；"
    "② 在镜像里装 bash（Debian/Ubuntu 系 `apt-get install -y bash`，"
    "Alpine 系 `apk add --no-cache bash`），并重新创建容器。"
)


def _is_missing_bash_failure(tool_name: str, message: str) -> bool:
    """判断一次 TSP 失败是否为「容器内没有 bash」。

    两个条件同时成立才算，缺一不可：

    1. **工具必须是 `execute_bash`**。`bash` 缺失只影响这个工具；文件类工具
       （`write_file` / `read_file`）由 gtsp 内部直接实现，不经过 `bash -c`。
       不加这条限定，将来别的工具报同样报文会被误归类，指向「换镜像」这个
       完全错误的出路。
    2. **报文必须命中完整 exec 特征**（见 `_MISSING_BASH_MARKERS`），不是
       只匹配 `not found` —— 否则「bash 在、命令不存在」会被误报。

    注意本函数**不**做宽泛兜底：不认识的失败返回 False，由调用方原样透出。
    """
    if tool_name != "execute_bash":
        return False
    # 两边都降为小写再比：真实报文里是 `$PATH`（大写），而 `exec` 一行的大小写
    # 在不同运行时/版本上并不稳定。统一小写化，避免因一个字母大小写让识别
    # **静默失效**（失效时表现为「什么都没变」，是这类补丁最常见的翻车方式）。
    lowered = (message or "").lower()
    return any(marker in lowered for marker in _MISSING_BASH_MARKERS)


def build_gtsp_command(raw_command: Optional[list[str]], workdir: str) -> list[str]:
    if raw_command is None:
        default_binary = appPaths.get_gtsp_binary_path()
        command = [default_binary, "--mode", "stdio"]
    else:
        command = list(raw_command)

    if "--workdir" not in command and workdir:
        command.extend(["--workdir", workdir])

    # 添加日志路径，使用用户可写目录（避免 App Translocation 只读问题）
    if "--log-path" not in command:
        log_path = os.path.join(appPaths.LOGS_DIR, "gtsp")
        os.makedirs(log_path, exist_ok=True)
        command.extend(["--log-path", log_path])

    return command


class TspAgentDriver(AgentDriver):
    def __init__(self, host: Any, config: AgentDriverConfig) -> None:
        super().__init__(host, config)
        self._client: Optional[TSPClient] = None
        self._tsp_tools: dict[str, llmApiUtil.OpenAITool] = {}
        # TSP driver 是唯一暴露容器工具的驱动（§6.1）：容器工具做的就是切换
        # gtsp 的执行环境，别的驱动没有 gtsp 可切。
        self._local_tools: list[llmApiUtil.OpenAITool] = funcToolService.get_tools(
            include_container_tools=True
        )
        self._connect_lock = asyncio.Lock()

        # 执行环境（只在内存，重启一律回宿主，§4.1）。
        # 环境状态**只由 connect() 成功时更新**：disconnect() 只关连接、清 client
        # 与工具列表，不改这两个字段。这样「关掉连接但逻辑上仍在容器」的中间态
        # 可表达，退出流程里 connect(HOST) 失败时环境保持 CONTAINER，下次工具
        # 调用会重连容器而不是悄悄回落宿主。
        self._exec_env: ExecEnv = ExecEnv.HOST
        self._current_container: Optional[ContainerTarget] = None

        # 构建连接参数（用于首次连接和后续按需重连）
        options = config.options
        work_dir = str(options.get("workdir") or host.agent_workdir)
        command_list = build_gtsp_command(options.get("command"), work_dir)
        timeout_sec = int(options.get("request_timeout_sec", _DEFAULT_REQUEST_TIMEOUT_SEC))
        self._connect_params: dict[str, Any] = {
            "command": command_list,
            "timeout_sec": timeout_sec,
            "include": options.get("tool_include") or None,
            "exclude": options.get("tool_exclude") or None,
        }

    # ─── 执行环境（§4.1 / §4.3）──────────────────────────────────────────

    @property
    def exec_env(self) -> ExecEnv:
        return self._exec_env

    @property
    def current_container(self) -> Optional[ContainerTarget]:
        return self._current_container

    @property
    def current_container_id(self) -> Optional[str]:
        """当前所在容器的 12 位短 ID；宿主环境为 None（对外的 `current_container_id`）。"""
        return self._current_container.container_id if self._current_container else None

    def get_exec_env(self) -> ExecEnvSnapshot:
        """上报当前执行环境；宿主态也返回快照（TSP 驱动始终支持环境切换）。"""
        container = self._current_container
        return ExecEnvSnapshot(
            exec_env=self._exec_env,
            current_container_id=self.current_container_id,
            container_name=container.container_name if container else None,
        )

    def _build_container_command(self, container: ContainerTarget) -> list[str]:
        """按容器类型构造 gtsp 拉起命令（§3.2 形态 A / B）。

        - 自建容器：`docker start -a -i <id>`——容器本身就是「gtsp + tini」，
          attach 即接管 stdio，关 stdin 后 gtsp 退出、容器自然停回 exited；
        - 外部容器：`docker exec -i <id> /tmp/gtsp ...`——外部容器的主进程必须
          保持运行，只能额外 exec 一个 gtsp 进程。

        两种形态不能互换：对自建容器用 exec 会因容器未 running 而失败；对外部
        容器用 start 会二次启动主进程。
        """
        docker = container_cli.get_cli()
        if container.is_managed:
            argv = containerService.build_container_start_argv(container.container_id)
        else:
            argv = containerService.build_container_exec_argv(container.container_id)
        return [docker.binary_path, *argv]

    def _is_client_connected(self) -> bool:
        """检查 TSP client 进程是否仍然存活。

        gtsp 进程可能因异常退出或被终止而留下僵尸 client 对象，
        此时 _client 非空但 process.returncode 已有值。后续 tool() 调用会
        因写入已关闭的 stdin 而超时或抛异常，需提前检测避免无效等待。
        """
        if self._client is None:
            return False
        process = self._client.process
        if process is None:
            return False
        # returncode 为 None 表示进程仍在运行，非 None 表示已退出
        return process.returncode is None

    async def _connect_target(
        self,
        env: ExecEnv,
        container: Optional[ContainerTarget],
    ) -> bool:
        """建立到指定环境的 stdio 连接并加载工具；**成功时才更新环境状态**。

        环境状态（`_exec_env` / `_current_container`）在函数末尾才更新：中途失败
        时环境保持原值，避免出现「说是容器、实际连的是宿主」的不一致状态。
        """
        assert (env == ExecEnv.CONTAINER) == (container is not None), (
            f"环境与容器参数不匹配: env={env}, container={container}"
        )

        if env == ExecEnv.CONTAINER:
            assert container is not None
            command = self._build_container_command(container)
            logger.info(
                "连接容器环境: agent_id=%s, target=%s",
                self.host.gt_agent.id, container.display_name,
            )
        else:
            command = list(self._connect_params["command"])
            logger.info("连接宿主环境: agent_id=%s", self.host.gt_agent.id)

        client: Optional[TSPClient] = None
        try:
            async def _spawn_and_initialize() -> TSPClient:
                nonlocal client
                # 先赋给外层的 client：若 initialize 抛错，except 分支才能拿到
                # 已拉起的进程并关掉它。否则容器内 gtsp 会一直挂着，且下次
                # docker start 会因容器已 running 而报 container_already_running。
                client = await TSPClient.from_stdio(
                    command,
                    # 必须用连接参数里的超时（默认 190s），**不能**用
                    # enter_timeout_sec：后者只是「拉起 + 握手」的等待上限，
                    # 用它会把容器内所有工具请求的响应上限压到 60s。
                    self._connect_params["timeout_sec"],
                ).start()
                await client.initialize(
                    client_info={"name": "agent_team.tsp_driver"},
                    include=self._connect_params.get("include"),
                    exclude=self._connect_params.get("exclude"),
                )
                return client

            client = await asyncio.wait_for(
                _spawn_and_initialize(), timeout=CONTAINER_ENTER_TIMEOUT_SEC
            )
        except asyncio.TimeoutError:
            logger.error(
                "TSP 连接超时: agent_id=%s, env=%s, timeout=%ss",
                self.host.gt_agent.id, env.value, CONTAINER_ENTER_TIMEOUT_SEC,
            )
            await self._close_client_quietly(client)
            return False
        except asyncio.CancelledError:
            # turn 被取消时也要把刚拉起的子进程收掉，否则容器内 gtsp 会一直挂着
            await self._close_client_quietly(client)
            raise
        except Exception as e:
            logger.error(
                "TSP 连接失败: agent_id=%s, env=%s, error=%s",
                self.host.gt_agent.id, env.value, e,
            )
            await self._close_client_quietly(client)
            return False

        # 连接成功：先换掉旧 client，再更新环境状态
        await self._close_client_quietly(self._client)
        self._client = client
        self._load_tsp_tools(client.tools)
        self._register_host_tools()
        previous_env = self._exec_env
        previous_container_id = self.current_container_id
        self._exec_env = env
        self._current_container = container
        logger.info(
            "TSP 连接成功: agent_id=%s, env=%s, container=%s, tools=%s",
            self.host.gt_agent.id, env.value,
            container.container_id if container else None,
            len(self._tsp_tools),
        )

        # 只在环境**实际变化**时广播（§8）：重连同一个环境不该刷 UI，
        # 否则容器断了自动重连会把「进入容器」这个动作重复上报。
        new_container_id = self.current_container_id
        if env != previous_env or new_container_id != previous_container_id:
            self._publish_container_state()
        return True

    def _publish_container_state(self) -> None:
        """广播当前执行环境（§8 `AGENT_CONTAINER_STATE_CHANGED`）。

        用 `publish` 而不是 `await publish`：这是状态通知，不该让环境切换的
        成功与否取决于订阅者；messageBus 自身也是同步投递。
        """
        try:
            messageBus.publish(
                MessageBusTopic.AGENT_CONTAINER_STATE_CHANGED,
                gt_agent=self.host.gt_agent,
                exec_env=self._exec_env.value,
                current_container_id=self.current_container_id,
            )
        except Exception:
            logger.warning(
                "广播执行环境变更失败（忽略）: agent_id=%s", self.host.gt_agent.id, exc_info=True,
            )

    @staticmethod
    async def _close_client_quietly(client: Optional[TSPClient]) -> None:
        """关闭 client 并吞掉异常：清理路径不能盖住真正的错误。"""
        if client is None:
            return
        try:
            await client.disconnect()
        except Exception:
            logger.warning("关闭 TSP client 失败（忽略）", exc_info=True)

    async def connect(
        self,
        env: ExecEnv = ExecEnv.HOST,
        container: Optional[ContainerTarget] = None,
    ) -> bool:
        """切换到指定执行环境；成功返回 True，失败返回 False（环境保持原值）。

        这是对外的环境切换入口（§4.3）。`enter_container` / `exit_to_host` 都
        通过它切环境，因此「切一半失败」不会留下不一致的状态。

        Args:
            env: 目标环境
            container: `env == CONTAINER` 时必传；HOST 时忽略

        Returns:
            是否切换成功
        """
        if env == ExecEnv.CONTAINER and container is None:
            logger.error("connect(CONTAINER) 缺少 container 参数: agent_id=%s", self.host.gt_agent.id)
            return False

        async with self._connect_lock:
            return await self._connect_target(env, container if env == ExecEnv.CONTAINER else None)

    async def disconnect(self) -> None:
        """关闭当前 stdio 连接并清空工具（**不重置环境状态**，§4.3）。

        为什么不能重置环境状态：`exit_to_host` 的流程是
        `disconnect()` → `connect(HOST)`。如果 disconnect 就把 `_exec_env` 改成
        HOST，那么 connect(HOST) 失败时状态就变成了「说在宿主、实际连不上」，
        Agent 下一次工具调用会去连宿主而不是重连容器，环境被静默改变。
        现在的写法保证：只有 connect 成功才改环境，失败则保持原环境。
        """
        async with self._connect_lock:
            await self._close_client_quietly(self._client)
            self._client = None
            self._tsp_tools = {}

    def _current_target_or_none(self) -> Optional[ContainerTarget]:
        return self._current_container if self._exec_env == ExecEnv.CONTAINER else None

    async def _ensure_connected(self) -> bool:
        """确保 TSP client 已连接，若断开则**只重连当前环境**。

        绝不跨环境回落（§4.3）：容器断开就重连容器，宿主断开就重连宿主。
        悄悄回落到宿主会让 Agent 的后续操作作用在错误的文件系统上——容器里写的
        文件突然写到宿主，这比直接失败危险得多。

        返回 True 表示连接成功（或已连接），False 表示失败。
        """
        async with self._connect_lock:
            # 再次检查，防止其他协程已完成连接
            if self._is_client_connected():
                return True

            logger.info(
                "TSP 服务未连接，尝试重连: agent_id=%s, env=%s",
                self.host.gt_agent.id, self._exec_env.value,
            )

            # 清理旧 client（只清连接，不动环境状态）
            await self._close_client_quietly(self._client)
            self._client = None

            # 注意：这里直接调用 `_connect_target`（已在锁内），不能再调
            # `connect()` —— `asyncio.Lock` 不可重入，会直接死锁。
            return await self._connect_target(self._exec_env, self._current_target_or_none())

    async def startup(self) -> None:
        await super().startup()

        # 使用 _ensure_connected 完成首次连接
        connected = await self._ensure_connected()
        if not connected:
            raise RuntimeError(f"TSP 首次连接失败: agent_id={self.host.gt_agent.id}")

        logger.info(f"TSP driver initialized: agent={self.host.gt_agent.id} command={self._connect_params['command']} tools={len(self._tsp_tools)}")

    async def shutdown(self) -> None:
        if self._client is None:
            await super().shutdown()
            return
        client = self._client
        self._client = None

        # 直接 disconnect 终止进程（不等待 shutdown request）
        # 避免 wait_for 取消导致 disconnect 也被取消
        try:
            await client.disconnect()
        except Exception as e:
            logger.warning("TSP disconnect 异常: agent_id=%s, error=%s", self.host.gt_agent.id, e)

        await super().shutdown()

    @property
    def host_managed_turn_loop(self) -> bool:
        return True

    def _register_host_tools(self) -> None:
        if self._client is None:
            raise RuntimeError(f"TSP client 尚未初始化: agent_id={self.host.gt_agent.id}")
        self.host.tool_registry.clear()

        for tool in self._local_tools:
            function_name = tool.function.name
            self.host.tool_registry.register(
                tool,
                funcToolService.run_tool_call,
                marks_turn_finish=function_name == "finish_action",
                self_interrupt=function_name == "reload_team",
            )

        for tool in self._tsp_tools.values():
            self.host.tool_registry.register(tool, self._execute_tsp_tool)
        self._apply_tool_policy()

    def _apply_tool_policy(self) -> None:
        configured_names = self.config.options.get("local_tool_names")
        if configured_names:
            self.host.tool_registry._set_enabled_tool_names(list(configured_names))
            return

        effective_specs = toolRegistry.build_runtime_allow_specs(
            self.config.options.get("tool_allow_specs"),
            is_root_leader=bool(self.config.options.get("is_root_leader")),
        )

        self.host.tool_registry.apply_tool_allow_specs(effective_specs)

    @property
    def turn_setup(self) -> AgentTurnSetup:
        return AgentTurnSetup(
            max_retries=_RUN_CHAT_TURN_MAX_RETRIES,
            hint_prompt=_RUN_CHAT_TURN_HINT,
            hint_prompt_error_action=_RUN_CHAT_TURN_ERROR_ACTION_HINT,
        )

    async def run_task_turn(self, task: GtScheculeTask, synced_count: int) -> None:
        raise RuntimeError("TspAgentDriver 不再直接执行 run_task_turn，请使用 Agent.run_task_turn")

    async def _execute_tsp_tool(
        self,
        function_args: str,
        context: roomService.ToolCallContext | None = None,
    ) -> dict[str, Any]:

        # 确保连接，若断开则自动重连
        if not self._is_client_connected():
            connected = await self._ensure_connected()
            if not connected:
                return {"success": False, "message": "TSP 服务已断开且重连失败，请重启 Agent 或联系管理员"}

        function_name = context.tool_name if context is not None else ""

        if not function_name:
            return {"success": False, "message": "TSP 工具调用失败: tool_name 为空"}
        try:
            parsed_args = json.loads(function_args)
        except json.JSONDecodeError as e:
            return {"success": False, "message": f"TSP 参数 JSON 解析失败: {e}"}

        try:
            call = ToolCall(name=function_name, input=parsed_args)
            result: ToolResult = await self._client.call_tool(call)
            # ToolResult.output 是原始类型（dict / list / str）
            return result.output
        except TSPException as e:
            # 连接断开类错误：清空 client 以便下次调用触发重连
            if e.code in (TSP_ERROR_STDOUT_CLOSED, TSP_ERROR_CONNECTION_CLOSED):
                logger.warning("TSP 连接断开: agent_id=%s, code=%s, message=%s", self.host.gt_agent.id, e.code, e.message)
                self._client = None
            # 名字不能叫 `result`：上面 `try` 里已有 `result: ToolResult`，同名会让
            # mypy 报 `Name "result" already defined`（两个分支的静态类型互不兼容）。
            failure: dict[str, Any] = {"success": False, "code": e.code, "message": e.message}
            # 业务层归纳叠加在协议层原文之上（`code` 保留，不覆盖）。
            # 只处理「容器里没有 bash」这一类，其余异常原样透出——所以这里是
            # **窄判定**（精确子串 + 工具名限定），不是宽泛 `except`。
            if _is_missing_bash_failure(function_name, e.message):
                logger.warning(
                    "容器内缺少 bash，execute_bash 不可用: agent_id=%s, raw=%s",
                    self.host.gt_agent.id, e.message,
                )
                failure["error_code"] = containerService.ERROR_CONTAINER_MISSING_BASH
                failure["message"] = _MISSING_BASH_MESSAGE
            return failure
        except Exception as e:
            logger.warning("TSP 工具执行异常: agent_id=%s, tool=%s, error=%s", self.host.gt_agent.id, function_name, e)
            # 兼容旧版 pytspclient 仍抛出 RuntimeError 的情况
            err_str = str(e)
            if isinstance(e, (RuntimeError, TimeoutError)) and (
                "TSP stdout closed" in err_str or "TSP request timeout" in err_str
            ):
                self._client = None
            return {"success": False, "message": f"TSP 工具调用失败: {e}"}

    def _load_tsp_tools(self, tools: list[Any]) -> None:
        resolved: dict[str, llmApiUtil.OpenAITool] = {}

        for tool in tools:
            # tools 是 TSPTool 对象，直接访问属性
            resolved[tool.name] = llmApiUtil.OpenAITool(
                function=llmApiUtil.OpenAIFunction(
                    name=tool.name,
                    description=tool.description,
                    parameters=llmApiUtil.OpenAIFunctionParameter(**tool.input_schema),
                ),
            )

        self._tsp_tools = resolved
