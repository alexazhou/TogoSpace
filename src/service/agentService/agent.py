import logging
from typing import List, Optional

from constants import AgentStatus, ContainerState, ExecEnv
from exception import TogoException
from model.coreModel.agentModel import ExecEnvState
from model.coreModel.containerModel import ContainerTarget
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtAgentHistory import GtAgentHistory
from service import containerService
from service.agentService.agentTaskConsumer import AgentTaskConsumer
from service.agentService.driver import (
    AgentDriver,
    AgentDriverConfig,
    ContainerCapableDriver,
)

logger = logging.getLogger(__name__)


class Agent:
    """AI Team Agent — facade 角色。

    Agent 本身负责：
    - 生命周期管理（startup / close）
    - 组件装配（task_consumer）
    - 执行环境切换（enter_container / exit_to_host）
    - 对外 API 入口（start_consumer_task 等）的一层转发

    Turn 级资源（driver, tool_registry, history）在 AgentTurnRunner 中，
    任务运行时状态与消费逻辑在 AgentTaskConsumer 中。
    AgentTurnRunner 由 AgentTaskConsumer 内部创建和持有。
    """


    # ─── 生命周期 ──────────────────────────────────────────────

    def __init__(
        self,
        gt_agent: GtAgent,
        system_prompt: str,
        driver_config: Optional[AgentDriverConfig] = None,
        agent_workdir: str = "",
        is_root_leader: bool = False,
    ):
        self.gt_agent: GtAgent = gt_agent
        self.system_prompt: str = system_prompt
        self.is_root_leader: bool = is_root_leader
        self.task_consumer: AgentTaskConsumer = AgentTaskConsumer(
            gt_agent=gt_agent,
            system_prompt=system_prompt,
            agent_workdir=agent_workdir,
            driver_config=driver_config,
        )

    @property
    def driver(self) -> AgentDriver:
        return self.task_consumer._turn_runner.driver

    @property
    def status(self) -> AgentStatus:
        return self.task_consumer.status

    @property
    def agent_workdir(self) -> str:
        """Agent 在宿主上的私有工作目录（容器内对应 `/workspace/self`）。"""
        return self.task_consumer.agent_workdir

    @property
    def is_active(self) -> bool:
        """检查 Agent 是否活跃。"""
        return self.task_consumer.status == AgentStatus.ACTIVE

    @property
    def host_managed_turn_loop(self) -> bool:
        """是否使用 host-managed turn loop（支持运行中即时消息插入）。"""
        return self.driver.host_managed_turn_loop

    async def startup(self) -> None:
        await self.driver.startup()

    async def close(self) -> None:
        self.task_consumer.status = AgentStatus.CLOSED
        self.stop_consumer_task()
        await self.driver.shutdown()
        self.task_consumer._turn_runner.tool_registry.clear()

    def inject_history_messages(self, items: List[GtAgentHistory]) -> None:
        self.task_consumer._turn_runner._history.replace(items)

    # ─── 执行环境 ──────────────────────────────────────────────

    def container_driver(self) -> ContainerCapableDriver | None:
        """返回支持容器沙箱的 driver；Native / Claude SDK 返回 None。"""
        return self.driver if isinstance(self.driver, ContainerCapableDriver) else None

    def require_container_driver(self) -> ContainerCapableDriver:
        """返回支持容器沙箱的 driver；不支持时抛 `container_invalid_params`。"""
        driver = self.container_driver()
        if driver is None:
            raise TogoException(
                "当前 Agent 的执行驱动不支持容器沙箱（仅支持 TSP 驱动）",
                containerService.ERROR_CONTAINER_INVALID_PARAMS,
            )
        return driver

    def get_exec_env_state(self) -> ExecEnvState | None:
        """读取当前执行环境；驱动不报告环境时返回 None。"""
        return self.driver.get_exec_env()

    async def enter_container(self, container_id: str) -> tuple[ContainerTarget, ContainerState]:
        """把 Agent 的执行环境切换到指定容器，返回 `(目标, 进入前状态)`。

        流程严格按 §5.1：
        1. `resolve_enter_target` 解析目标并取实时状态（含 12 位 ID 校验、存在性、
           架构；只做一次 `docker inspect`）；
        2. 同容器幂等：已在目标容器且连接存活 → 直接成功；
        3. 状态判定（`validate_enter_state`）：自建只收 created/exited，外部只收 running；
        4. 外部容器先注入 gtsp（自建在 create 时已注入）；
        5. `driver.disconnect()` → `driver.connect(CONTAINER, target)`；
        6. connect 失败则重连宿主（**本次进入失败**，不属于运行中自动回落）。

        Raises:
            TogoException: 驱动不支持 / 任一步失败
        """
        driver = self.require_container_driver()
        target, state = await containerService.resolve_enter_target(
            self.gt_agent.id, container_id
        )

        if (
            driver.exec_env == ExecEnv.CONTAINER
            and driver.current_container_id == container_id
            and driver.is_connected()
        ):
            # 幂等：已在目标容器且连接存活。不重连（重连会打断正在进行的 turn）。
            # 连接已断时不能返回成功，要走下面的正常流程重新拉起。
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
        await driver.disconnect()
        ok = await driver.connect(ExecEnv.CONTAINER, target)
        if not ok:
            # 进入失败：本次调用内把宿主连接恢复出来，避免 Agent 失去可用环境。
            # 注意这是「该操作自身的失败处理」，不是运行中的自动回落（§4.3）。
            restored = await driver.connect(ExecEnv.HOST)
            if not restored:
                logger.error(
                    "进入容器失败且无法恢复宿主连接: agent_id=%s, container=%s",
                    self.gt_agent.id, container_id,
                )
            raise TogoException(
                f"进入容器 {container_id} 失败：容器内 gtsp 启动或握手未成功。"
                "请确认镜像内 /tmp 可写且能执行注入的二进制。",
                containerService.ERROR_CONTAINER_ENTER_FAILED,
            )

        logger.info(
            "已进入容器: agent_id=%s, container=%s, managed=%s, from_state=%s",
            self.gt_agent.id, container_id, target.is_managed, state,
        )
        return target, state

    async def exit_to_host(self) -> None:
        """把 Agent 的执行环境切回宿主（§5.2）。

        顺序：`disconnect()`（关容器连接 → 自建容器自然停回 exited）→
        `connect(HOST)`。connect 失败时环境保持 CONTAINER，由 Agent 重试——
        不能假装成功，否则后续工具会打在错误环境上。

        Raises:
            TogoException: 驱动不支持 / 宿主连接失败
        """
        driver = self.require_container_driver()
        if driver.exec_env == ExecEnv.HOST:
            # 已经在宿主：幂等成功，不重连（重连会打断 turn）
            return

        # 先断开容器连接：关 stdin 后容器内 gtsp 退出，自建容器随之停回 exited
        await driver.disconnect()

        ok = await driver.connect(ExecEnv.HOST)
        if not ok:
            raise TogoException(
                "退出容器失败：无法重新连接宿主环境。当前逻辑环境仍为容器，"
                "请重试 exit_to_host。",
                containerService.ERROR_CONTAINER_ENTER_FAILED,
            )

        logger.info("已退回到宿主环境: agent_id=%s", self.gt_agent.id)

    async def switch_to_host(self) -> bool:
        """把 Agent 切回宿主环境；返回是否处于宿主（best-effort，不抛异常）。

        为什么清理容器前必须先切回宿主：Agent 当前若正在某个容器里，`docker rm -f`
        会把它的 stdio 连接打断，Driver 会进入「说在容器、实际连不上」的状态。
        §7 的做法是显式断开再重连宿主。
        """
        driver = self.container_driver()
        if driver is None or driver.exec_env == ExecEnv.HOST:
            return True

        try:
            await self.exit_to_host()
        except Exception:
            logger.warning("清理容器前切回宿主失败: agent_id=%s", self.gt_agent.id, exc_info=True)
            return False

        logger.info("清理容器前已切回宿主: agent_id=%s", self.gt_agent.id)
        return True

    # ─── 任务管理 ──────────────────────────────────────────────

    def start_consumer_task(self) -> None:
        """如果没有消费协程在运行，则启动一个。"""
        if self.status == AgentStatus.CLOSED:
            logger.info(f"Agent {self.gt_agent.name}(agent_id={self.gt_agent.id}) 已经被关闭，忽略调度唤醒")
            return
        self.task_consumer.start()

    def stop_consumer_task(self) -> None:
        """停止当前 Agent 的消费协程。"""
        self.task_consumer.stop()

    def cancel_current_turn(self) -> bool:
        """人工停止当前 turn。返回 True 表示已发出取消信号。"""
        return self.task_consumer.cancel_current_turn()
