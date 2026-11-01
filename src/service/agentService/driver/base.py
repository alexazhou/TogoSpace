from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from constants import DriverType, ExecEnv
from model.coreModel.agentModel import ExecEnvState
from model.coreModel.containerModel import ContainerTarget
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtScheculeTask import GtScheculeTask
from service.agentService.agentHistoryStore import AgentHistoryStore
from service.agentService.toolRegistry import AgentToolRegistry


@dataclass
class AgentDriverConfig:
    driver_type: DriverType = DriverType.NATIVE
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentTurnActionResult:
    ok: bool
    message: str
    turn_finished: bool = False


@dataclass
class AgentTurnSetup:
    max_retries: int = 1
    hint_prompt: str = ""
    hint_prompt_error_action: str = ""


class AgentDriverHost(Protocol):
    gt_agent: GtAgent
    system_prompt: str
    agent_workdir: str
    _history: AgentHistoryStore
    tool_registry: AgentToolRegistry

    async def execute_pending_tools(self) -> None:
        ...


@runtime_checkable
class ContainerCapableDriver(Protocol):
    """支持容器沙箱的驱动契约（目前由 `TspAgentDriver` 实现）。

    Agent 用 `isinstance(driver, ContainerCapableDriver)` 做能力探测：只有实现
    本协议的驱动才有「当前执行环境」这个概念，才能被 `enter_container` /
    `exit_to_host` 操作。用协议而不是具体驱动类型，避免 Agent 依赖 driver 的
    实现模块（那会把 gtsp 相关导入链拉进来）。
    """

    @property
    def exec_env(self) -> ExecEnv:
        ...

    @property
    def current_container_id(self) -> str | None:
        ...

    def get_exec_env(self) -> ExecEnvState:
        ...

    def is_connected(self) -> bool:
        ...

    async def connect(
        self,
        env: ExecEnv = ExecEnv.HOST,
        container: ContainerTarget | None = None,
    ) -> bool:
        ...

    async def disconnect(self) -> None:
        ...


class AgentDriver:
    def __init__(self, host: AgentDriverHost, config: AgentDriverConfig):
        self.host = host
        self.config = config
        self._started: bool = False
        self._startup_loop: asyncio.AbstractEventLoop | None = None

    @property
    def driver_type(self) -> DriverType:
        return self.config.driver_type

    @property
    def started(self) -> bool:
        return self._started

    @property
    def host_managed_turn_loop(self) -> bool:
        return False

    def get_exec_env(self) -> ExecEnvState | None:
        """当前执行环境快照；不支持容器沙箱的驱动返回 None。

        None 表示「不报告环境」，活动记录不会写入 `exec_env` 相关字段——
        Native / Claude SDK 驱动保持原有行为，不为它们伪造一个宿主环境。
        """
        return None

    async def startup(self) -> None:
        self._started = True
        self._startup_loop = asyncio.get_running_loop()

    async def shutdown(self) -> None:
        if self._startup_loop is not None:
            current_loop = asyncio.get_running_loop()
            assert current_loop is self._startup_loop, (
                f"AgentDriver.shutdown() 必须在 startup() 所用的事件循环上调用，"
                f"否则 asyncio IO 对象无法正常关闭。"
                f"startup_loop={id(self._startup_loop)}, current_loop={id(current_loop)}"
            )
        self._started = False
        self._startup_loop = None

    @property
    def turn_setup(self) -> AgentTurnSetup:
        return AgentTurnSetup()

    async def run_task_turn(self, task: GtScheculeTask, synced_count: int) -> None:
        raise NotImplementedError

    async def cancel_turn(self) -> None:
        """人工取消当前 turn 时调用。子类可覆写以执行 driver 特有的清理。"""
        pass
