from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Optional

from pytspclient import TSPClient, TSPException, ToolResult, ToolCall
from pytspclient.types import TSP_ERROR_STDOUT_CLOSED, TSP_ERROR_CONNECTION_CLOSED

import appPaths
from constants import (
    CONTAINER_ENTER_TIMEOUT_SEC,
    ERROR_DOCKER_CLI_NOT_FOUND,
    ContainerState,
    ExecEnv,
    MessageBusTopic,
)
from exception import TogoException
from model.coreModel import ContainerTarget, ExecEnvState
from service.agentService.driver.base import AgentDriverConfig

from service import containerService, funcToolService, roomService, messageBus
from service.funcToolService import toolArgsValidation
from toolResult import error_result, invalid_json_args
from service.agentService import toolRegistry
from model.dbModel.gtScheduleJob import GtScheduleJob
from util import containerUtil, llmApiUtil

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
        # 注册面用全量工具（#166-A）：模型暴露面由 registry 按 `tool_visible` 过滤。
        self._local_tools: list[llmApiUtil.OpenAITool] = funcToolService.get_all_tools(
            include_container_tools=True
        )
        self._connect_lock = asyncio.Lock()

        # 执行环境（只在内存，重启一律回宿主，§4.1）。
        # `_current_container` 是唯一状态源：None = 宿主，非 None = 容器；
        # `exec_env` / `current_container_id` 都由它派生，避免两个字段不同步。
        # 环境状态**只由 connect() 成功时更新**：disconnect() 只关连接、清 client
        # 与工具列表，不改这个字段。这样「关掉连接但逻辑上仍在容器」的中间态
        # 可表达，退出流程里 connect(HOST) 失败时环境保持容器，下次工具调用会
        # 重连容器而不是悄悄回落宿主。
        self._current_container: Optional[ContainerTarget] = None

        # 已挂起的容器会话池：container_id（12 位短 ID）→ 仍存活的 TSPClient。
        #
        # `exit_to_host(keep_alive=True)` 把当前容器的 client **移交**到这里，而不是
        # 断开它：容器内 gtsp 阻塞在读 stdin，只要 Python 侧不关管道就不会收到 EOF，
        # 于是自建容器保持 running、外部容器的会话与其进程组都不被清理。下次
        # `enter_container` 同一容器时直接把这个 client 切回活跃态，省掉 docker
        # 拉起与 initialize 握手。
        #
        # 池里的 client **不属于当前活跃环境**：`_current_container` 仍是唯一状态源
        # （None = 宿主），所以池表达的是「暂停待复用」，不是多容器并发执行。
        # 读写一律在 `_connect_lock` 内（`shutdown` 的清理例外）。
        self._suspended_clients: dict[str, TSPClient] = {}

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
        """当前执行环境；容器目标为空即宿主。"""
        return ExecEnv.CONTAINER if self._current_container is not None else ExecEnv.HOST

    @property
    def current_container(self) -> Optional[ContainerTarget]:
        return self._current_container

    @property
    def current_container_id(self) -> Optional[str]:
        """当前所在容器的 12 位短 ID；宿主环境为 None（对外的 `current_container_id`）。"""
        return self._current_container.container_id if self._current_container else None

    def get_exec_env(self) -> ExecEnvState:
        """上报当前执行环境；宿主态也返回快照（TSP 驱动始终支持环境切换）。"""
        container = self._current_container
        return ExecEnvState(
            exec_env=self.exec_env,
            current_container_id=self.current_container_id,
            container_name=container.container_name if container else None,
        )

    async def enter_container(self, container_id: str) -> tuple[ContainerTarget, ContainerState]:
        """切换到指定容器，返回 `(目标, 进入前状态)`（§5.1）。

        流程：
        1. `resolve_enter_target` 解析目标并取实时状态（含 12 位 ID 校验、存在性、
           架构；只做一次 `docker inspect`）；
        2. 同容器幂等：已在目标容器且连接存活 → 直接成功；
        3. 状态判定（`validate_enter_state`）：自建只收 created/exited，外部只收 running；
        4. 外部容器先注入 gtsp（自建在 create 时已注入）；
        5. `disconnect()` → `connect(CONTAINER, target)`；
        6. connect 失败则重连宿主（**本次进入失败**，不属于运行中自动回落）。

        Raises:
            TogoException: 任一步失败
        """
        try:
            target, state = await containerService.resolve_enter_target(
                self.host.gt_agent.id, container_id
            )
        except Exception:
            # 解析失败意味着容器已查不到/已删除（典型 `container_lost`）：这次进入
            # 必然失败，但**挂起池里那份记录也随之作废**，必须在这里淘汰。
            # 否则它永远等不到「下次健康检测」——健康检测在 resolve 之后，走不到；
            # 结果就是死 client 与它的管道 fd 一直留着（step1 §3.3 步骤 2 要求
            # 「自动清理失效的挂起记录」）。清完原样抛出，错误码不变。
            await self._evict_suspended(container_id)
            raise

        if (
            self.current_container is not None
            and self.current_container_id == container_id
            and self.is_connected()
        ):
            # 幂等：已在目标容器且连接存活。不重连（重连会打断正在进行的 turn）。
            # 连接已断时不能返回成功，要走下面的正常流程重新拉起。
            return target, state

        # 挂起池命中：直接复用保活中的会话，跳过状态校验、注入、拉起与握手。
        # 顺序上必须在 `validate_enter_state` 之前——保活期间自建容器本来就处于
        # running，按正常校验会被判成「异常残留」拦下。
        if await self._resume_suspended(target, container_id):
            return target, state

        if state is ContainerState.UNKNOWN:
            raise TogoException(
                f"无法查询容器 {container_id} 的状态（Docker 不可用或查询超时），"
                "请稍后重试",
                containerService.ERROR_CONTAINER_ENTER_FAILED,
            )
        containerService.validate_enter_state(target, state)

        if not target.is_managed:
            # 外部容器：镜像里没有 gtsp，必须进前注入
            await containerService.prepare_container_gtsp(target)

        # 切环境：先断开再显式连接。disconnect 不改环境状态（见 Driver 注释），
        # 所以 connect 失败时环境仍是旧值，由下面的失败分支显式处理。
        await self.disconnect()
        ok = await self.connect(ExecEnv.CONTAINER, target)
        if not ok:
            # 进入失败：本次调用内把宿主连接恢复出来，避免 Agent 失去可用环境。
            # 注意这是「该操作自身的失败处理」，不是运行中的自动回落（§4.3）。
            restored = await self.connect(ExecEnv.HOST)
            if not restored:
                logger.error(
                    "进入容器失败且无法恢复宿主连接: agent_id=%s, container=%s",
                    self.host.gt_agent.id, container_id,
                )
            raise TogoException(
                f"进入容器 {container_id} 失败：容器内 gtsp 启动或握手未成功。"
                "请确认镜像内 /tmp 可写且能执行注入的二进制。",
                containerService.ERROR_CONTAINER_ENTER_FAILED,
            )

        logger.info(
            "已进入容器: agent_id=%s, container=%s, managed=%s, from_state=%s",
            self.host.gt_agent.id, container_id, target.is_managed, state,
        )
        return target, state

    async def _evict_suspended(self, container_id: str) -> None:
        """摘掉某容器的挂起记录并关闭它（best-effort，不抛异常）。

        用于「该容器这条线已经不可用」的路径：容器被外部删除/查不到、或改为非保活
        退出。调用方随后按各自的错误码上报，这里只负责不让死 client 与管道 fd 滞留。
        """
        async with self._connect_lock:
            client = self._suspended_clients.pop(container_id, None)
        await self._close_client_quietly(client)

    async def _resume_suspended(
        self, target: ContainerTarget, container_id: str
    ) -> bool:
        """尝试用挂起池中的存活会话直接切回容器；命中返回 `True`。

        命中时**不**调用 `validate_enter_state` / `prepare_container_gtsp` /
        `docker start|exec` / `initialize`——保活的会话已经握手完成，重新拉起既慢
        又会打断容器内仍在运行的后台进程。

        挂起期间容器被外部停止或删除时，池里的 client 进程已退出：这里清掉死对象
        并返回 `False`，由调用方回退到正常进入流程（状态异常时按既有错误码报错）。
        """
        dead: Optional[TSPClient] = None
        previous_client: Optional[TSPClient] = None
        take_over = False

        async with self._connect_lock:
            suspended = self._suspended_clients.pop(container_id, None)
            if suspended is not None:
                if self._is_client_alive(suspended):
                    previous_client = self._client
                    self._client = suspended
                    self._tsp_tools = {}
                    take_over = True
                else:
                    dead = suspended

        if not take_over:
            if dead is not None:
                await self._close_client_quietly(dead)
                logger.info(
                    "挂起会话已失效，回退正常进入流程: agent_id=%s, container=%s",
                    self.host.gt_agent.id, container_id,
                )
            return False

        assert self._client is not None
        # 关闭切换前的活跃连接：可能是宿主 client，也可能是跨容器直接切换时的
        # 另一个容器 client。必须在锁外关（disconnect 会 await）。
        if previous_client is not None and previous_client is not self._client:
            await self._close_client_quietly(previous_client)

        self._load_tsp_tools(self._client.tools)
        self._register_host_tools()

        previous_env = self.exec_env
        previous_container_id = self.current_container_id
        self._current_container = target
        logger.info(
            "复用挂起会话进入容器: agent_id=%s, container=%s, managed=%s, tools=%s",
            self.host.gt_agent.id, target.container_id, target.is_managed,
            len(self._tsp_tools),
        )
        if self.exec_env != previous_env or self.current_container_id != previous_container_id:
            self._publish_container_state()
        return True

    async def exit_to_host(self, keep_alive: bool = False) -> bool:
        """切回宿主环境（§5.2），成功时返回 `True`。

        `keep_alive=False`（默认）沿用原行为：断开容器连接 → 容器内 gtsp 退出，
        自建容器随之停回 exited。

        `keep_alive=True` 改为「保活挂起」：**不**断开该连接，而是把 client 移交到
        `_suspended_clients[container_id]`，容器与其中的后台进程继续存活，下次
        `enter_container` 同一容器时直接复用（见 `_resume_suspended`）。

        两种模式共同点：**只有 `connect(HOST)` 成功才改环境状态**，失败时环境保持
        CONTAINER，由调用方重试——不能假装成功，否则后续工具会打在错误环境上。
        """
        if self.current_container is None:
            # 已经在宿主：幂等成功，不重连（重连会打断 turn）
            return True

        container_id = self.current_container_id
        assert container_id is not None, "current_container 非空时必须有 container_id"

        # 待关闭的旧连接统一收集，锁外关闭：disconnect() 要 await，不能持锁做 IO。
        to_close: list[Optional[TSPClient]] = []

        if not keep_alive:
            # 顺手清掉同容器的历史挂起对象：此前保活过、后来又用非保活方式退出时，
            # 池里那个 client 已经没人会复用了，留着只会泄漏一个子进程。
            await self._evict_suspended(container_id)
            await self.disconnect()
        else:
            async with self._connect_lock:
                client = self._client
                if client is not None:
                    if self._is_client_alive(client):
                        stale = self._suspended_clients.get(container_id)
                        if stale is not None and stale is not client:
                            to_close.append(stale)
                        self._suspended_clients[container_id] = client
                    else:
                        # 进程已死：不进池——复用时会立刻判死，等于占个位子。
                        to_close.append(client)
                    self._client = None
                    self._tsp_tools = {}
                # 注意：**不修改** `_current_container`。环境状态只由 connect() 成功
                # 时更新（见 __init__ 注释），这里改了会在 connect(HOST) 失败时
                # 谎报「已在宿主」。
            for client in to_close:
                await self._close_client_quietly(client)
            logger.info(
                "已挂起容器会话: agent_id=%s, container=%s, suspended_count=%s",
                self.host.gt_agent.id, container_id, len(self._suspended_clients),
            )

        ok = await self.connect(ExecEnv.HOST)
        if not ok:
            raise TogoException(
                "退出容器失败：无法重新连接宿主环境。当前逻辑环境仍为容器，"
                "请重试 exit_to_host。",
                containerService.ERROR_CONTAINER_ENTER_FAILED,
            )

        logger.info(
            "已退回到宿主环境: agent_id=%s, keep_alive=%s",
            self.host.gt_agent.id, keep_alive,
        )
        return True

    def _build_container_command(self, container: ContainerTarget) -> list[str]:
        """按容器类型构造 gtsp 拉起命令（§3.2 形态 A / B）。

        - 自建容器：`docker start -a -i <id>`——容器本身就是「gtsp + tini」，
          attach 即接管 stdio，关 stdin 后 gtsp 退出、容器自然停回 exited；
        - 外部容器：`docker exec -i <id> /tmp/gtsp ...`——外部容器的主进程必须
          保持运行，只能额外 exec 一个 gtsp 进程。

        两种形态不能互换：对自建容器用 exec 会因容器未 running 而失败；对外部
        容器用 start 会二次启动主进程。
        """
        docker_binary = containerUtil.resolve_docker_binary_path()
        if docker_binary is None:
            # 与 DockerCli 同一套探测逻辑；这里直接转成工具层可识别的错误码。
            raise TogoException(
                "未找到 docker 命令，请确认 Docker 已安装并在 PATH 中",
                ERROR_DOCKER_CLI_NOT_FOUND,
            )
        return [
            docker_binary,
            *containerUtil.build_container_attach_argv(
                container.container_id, is_managed=container.is_managed
            ),
        ]

    def is_connected(self) -> bool:
        """当前 stdio 连接是否仍存活（供 Agent 判定「已在目标容器」是否幂等）。"""
        return self._is_client_connected()

    @staticmethod
    def _is_client_alive(client: Optional[TSPClient]) -> bool:
        """该 client 的 gtsp 子进程是否仍存活（挂起池健康检测与当前连接同一口径）。"""
        if client is None:
            return False
        process = client.process
        if process is None:
            return False
        # returncode 为 None 表示进程仍在运行，非 None 表示已退出
        return process.returncode is None

    def _is_client_connected(self) -> bool:
        """检查当前 TSP client 进程是否仍然存活。

        gtsp 进程可能因异常退出或被终止而留下僵尸 client 对象，
        此时 _client 非空但 process.returncode 已有值。后续 tool() 调用会
        因写入已关闭的 stdin 而超时或抛异常，需提前检测避免无效等待。
        """
        return self._is_client_alive(self._client)

    async def _connect_target(
        self,
        env: ExecEnv,
        container: Optional[ContainerTarget],
    ) -> bool:
        """建立到指定环境的 stdio 连接并加载工具；**成功时才更新环境状态**。

        `_current_container` 在函数末尾才更新：中途失败时环境保持原值，避免
        出现「说是容器、实际连的是宿主」的不一致状态。
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
        previous_env = self.exec_env
        previous_container_id = self.current_container_id
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
            container = self._current_container
            messageBus.publish(
                MessageBusTopic.AGENT_CONTAINER_STATE_CHANGED,
                gt_agent=self.host.gt_agent,
                exec_env=self.exec_env.value,
                current_container_id=self.current_container_id,
                # 容器名供前端徽标 hover 展示；退出到宿主时为 None
                container_name=container.container_name if container else None,
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
        `disconnect()` → `connect(HOST)`。如果 disconnect 就把
        `_current_container` 清空，那么 connect(HOST) 失败时状态就变成了
        「说在宿主、实际连不上」，Agent 下一次工具调用会去连宿主而不是重连
        容器，环境被静默改变。现在的写法保证：只有 connect 成功才改环境，
        失败则保持原环境。
        """
        async with self._connect_lock:
            await self._close_client_quietly(self._client)
            self._client = None
            self._tsp_tools = {}

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
                self.host.gt_agent.id, self.exec_env.value,
            )

            # 清理旧 client（只清连接，不动环境状态）
            await self._close_client_quietly(self._client)
            self._client = None

            # 注意：这里直接调用 `_connect_target`（已在锁内），不能再调
            # `connect()` —— `asyncio.Lock` 不可重入，会直接死锁。
            return await self._connect_target(self.exec_env, self._current_container)

    async def startup(self) -> None:
        await super().startup()

        # 使用 _ensure_connected 完成首次连接
        connected = await self._ensure_connected()
        if not connected:
            raise RuntimeError(f"TSP 首次连接失败: agent_id={self.host.gt_agent.id}")

        logger.info(f"TSP driver initialized: agent={self.host.gt_agent.id} command={self._connect_params['command']} tools={len(self._tsp_tools)}")

    async def shutdown(self) -> None:
        # 挂起池必须一并清掉：它们是不属于当前环境的独立子进程，漏掉就是孤儿进程
        # 泄漏（自建容器还会一直保持 running）。逐个独立捕获异常，单个失败不影响
        # 其余 client 与 super().shutdown()。
        suspended = list(self._suspended_clients.values())
        self._suspended_clients.clear()
        for client in suspended:
            try:
                await client.disconnect()
            except Exception as e:
                logger.warning(
                    "关闭挂起 TSP client 异常: agent_id=%s, error=%s",
                    self.host.gt_agent.id, e,
                )
        if suspended:
            logger.info(
                "已清理挂起会话: agent_id=%s, count=%s",
                self.host.gt_agent.id, len(suspended),
            )

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
                # `console` 只有路由到 reload 子命令才自中断，靠参数判定（#154 D6）。
                self_interrupt_predicate=funcToolService.get_self_interrupt_predicate(function_name),
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

    async def run_task_turn(self, job: GtScheduleJob, synced_count: int) -> None:
        raise RuntimeError("TspAgentDriver 不再直接执行 run_task_turn，请使用 Agent.run_task_turn")

    async def _execute_tsp_tool(
        self,
        function_args: str | dict[str, Any],
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
        if isinstance(function_args, dict):
            # 转发路径（handler 契约 `str | dict`）：已组装好，无需 JSON 解析。
            parsed_args = function_args
        else:
            try:
                parsed_args = json.loads(function_args)
            except json.JSONDecodeError as e:
                return invalid_json_args(function_name, function_args, e)

        # 远端工具的参数校验：schema 来自 `self._tsp_tools[name].function.parameters`
        # ——它与 `_load_tsp_tools()` 用 `OpenAIFunctionParameter(**tool.input_schema)`
        # 构造、下发给模型的是同一份结构，所以没有第二份真相（小马哥 1015 裁定 (a')）。
        # 远端 schema 由对端决定，可能不合规范 ⇒ 取不到/schema 非法时本函数返回 None
        # （已记 warning）并**继续执行**，绝不因为 schema 坏了把正常调用拦死。
        remote_schema = toolArgsValidation.get_remote_tool_parameters_schema(
            function_name, self._tsp_tools.get(function_name),
        )
        if remote_schema is not None:
            invalid_args = toolArgsValidation.validate_tool_args(
                function_name, parsed_args, schema=remote_schema,
            )
            if invalid_args is not None:
                logger.warning(
                    "TSP 工具参数校验未通过: agent_id=%s, tool=%s, error_code=%s, args=%r",
                    self.host.gt_agent.id, function_name, invalid_args.get("error_code"), parsed_args,
                )
                return invalid_args

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
            # 协议层错误原样透出，不做业务层归纳；错误语义的归类由 gtsp 负责。
            # 名字不能叫 `result`：上面 `try` 里已有 `result: ToolResult`，同名会让
            # mypy 报 `Name "result" already defined`（两个分支的静态类型互不兼容）。
            return {"success": False, "code": e.code, "message": e.message}
        except Exception as e:
            logger.warning("TSP 工具执行异常: agent_id=%s, tool=%s, error=%s", self.host.gt_agent.id, function_name, e)
            # 兼容旧版 pytspclient 仍抛出 RuntimeError 的情况
            err_str = str(e)
            if isinstance(e, (RuntimeError, TimeoutError)) and (
                "TSP stdout closed" in err_str or "TSP request timeout" in err_str
            ):
                self._client = None
            return error_result(e, prefix="TSP 工具调用失败: ")

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
