from service import funcToolService
from model.dbModel.gtAgentTask import GtAgentTask

from .base import AgentDriver, AgentTurnSetup

_RUN_CHAT_TURN_HINT = (
    "你必须通过调用工具来行动。如果你不需要发言，或者已经完成了所有行动，"
    "请务必调用 finish_chat_turn 结束本轮（即跳过）。"
)
_RUN_CHAT_TURN_MAX_RETRIES = 3


class NativeAgentDriver(AgentDriver):
    @property
    def host_managed_turn_loop(self) -> bool:
        return True

    async def startup(self) -> None:
        await super().startup()
        self.host.tool_registry.clear()
        for tool in funcToolService.get_tools():
            function_name = tool.function.name
            self.host.tool_registry.register(
                tool,
                funcToolService.run_tool_call,
                marks_turn_finish=function_name == "finish_chat_turn",
            )

    @property
    def turn_setup(self) -> AgentTurnSetup:
        return AgentTurnSetup(
            max_retries=_RUN_CHAT_TURN_MAX_RETRIES,
            hint_prompt=_RUN_CHAT_TURN_HINT,
        )

    async def run_chat_turn(self, task: GtAgentTask, synced_count: int) -> None:
        raise RuntimeError("NativeAgentDriver 不再直接执行 run_chat_turn，请使用 Agent.run_chat_turn")
