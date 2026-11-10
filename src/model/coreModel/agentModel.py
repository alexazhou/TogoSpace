from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from constants import AgentStatus, ExecEnv
from model.dbModel.gtAgent import GtAgent
from util import jsonUtil


class ExecEnvState(BaseModel):
    """某个 Agent 当前执行环境的快照。

    Driver 上报、活动记录 metadata、Agent 列表 / 详情共用同一个结构；
    只存在于 Driver 内存，不落库：未加载的 Agent 一律回落到宿主。

    `container_name` 用于活动记录展示；`list_containers` 工具返回时会显式
    只挑 `exec_env` / `current_container_id`，不把整个 state 下发给模型。
    """

    model_config = ConfigDict(extra="forbid")

    exec_env: ExecEnv = ExecEnv.HOST
    current_container_id: str | None = None
    container_name: str | None = None


class AgentContextUsage(BaseModel):
    """某个 Agent 当前上下文占用快照（可选字段，未装载时整体为 None）。

    ## 为什么分母叫 `limit_tokens` 而不是 `total_tokens`

    `total_tokens` 读起来像「模型窗口上限」，但三级色温的分档基准必须与
    **压缩触发线**用同一个分母，否则会出现「真到压缩线却只有 74%」的错位：
    `context_window - reserve_output = 114688`，`trigger = floor(114688 × 0.85) = 97484`，
    `97484 / 131072 = 74.37%`。用窗口上限做分母时，橙红区在数学上不可达。
    所以分母取「可用于 prompt 的容量」（`compact.calc_hard_limit_tokens`），
    另设 `context_window_tokens` 只做 tooltip 展示、**不参与任何计算**。

    ## `ratio` 可能 > 1

    `used_tokens` 是 `HistoryTokenSnapshot.current_tokens`（有上游真值锚点时
    = 真值 + 锚点行起的尾部估算，否则为全量本地估算），`limit_tokens` 是配置值，
    两者口径不同，真实存在 `used > limit` 的窄窗口。这里**不做 clamp**：
    把超限压成 1.0 会让「已经超出容量」这个诊断信息消失，而前端画宽度本来就要
    `Math.min(100, …)` 兜底——clamp 放在唯一需要它的地方（渲染），别丢数据。
    """

    model_config = ConfigDict(extra="forbid")

    used_tokens: int
    """分子：当前上下文长度口径（`AgentHistoryStore.token_snapshot().current_tokens`）。"""

    limit_tokens: int
    """分母：可用于 prompt 的容量 = context_window − reserve_output。"""

    compact_trigger_tokens: int
    """压缩触发线（后端唯一权威值，前端红区判据就是 `used >= 它`）。"""

    context_window_tokens: int
    """模型上下文窗口原始配置值。**仅供 tooltip 展示，不参与任何计算。**"""

    ratio: float
    """`used_tokens / limit_tokens`，未 clamp（见类注释）。"""


class AgentRuntimeInfo(BaseModel):
    """Agent 列表 / 详情的外层：原始 DB 对象 + 运行时状态。

    `agent` 直接持有 `GtAgent`，序列化复用 ORM 自带的 `to_json()`；运行时字段
    放在外层，不混进 DB 字段里。执行环境整体用 `ExecEnvState` 表达。
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    agent: GtAgent
    status: AgentStatus
    """运行时状态（IDLE / ACTIVE / FAILED / CLOSED），来自内存而非 DB。"""

    exec_env_state: ExecEnvState = Field(default_factory=ExecEnvState)
    container_count: int = 0

    context_usage: AgentContextUsage | None = None
    """上下文占用快照。**`None` 表示「不知道」，不是「0」**。

    `None` 的三种情形：Agent 未装载进内存（进程重启后未 `load_team_agents`）、
    请求的是 Operator / SYSTEM 这类负 id 虚拟身份、或模型配置解析失败。
    这三种情况下前端**整条进度条不渲染**——渲染成 0% 是在撒谎，用户会以为
    上下文是空的，而真实情况是「这个数没算出来」。

    列表接口（`get_team_agent_list_info`）不填充该字段：为每个成员各算一次
    要遍历历史，列表页按成员数线性放大代价，而列表页并不展示它。
    """

    @field_serializer("agent")
    def _serialize_agent(self, value: GtAgent) -> dict[str, Any]:
        data = jsonUtil.object_to_json_data(value)
        # 接口契约要求 driver 用小写 value，jsonUtil 默认取枚举的 name
        data["driver"] = value.driver.value
        return data

    @field_serializer("status")
    def _serialize_status(self, value: AgentStatus) -> str:
        return value.name
