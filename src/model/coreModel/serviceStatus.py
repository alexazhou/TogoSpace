from __future__ import annotations

from dataclasses import dataclass, field

from constants import ServiceStatus


@dataclass
class ServiceStatusInfo:
    """单个服务的生命周期状态载体。

    - ``status``：当前生命周期状态，是「是否已启动」的唯一真源。
    - ``fine`` / ``error``：最近一次启动是否正常、失败原因。
    - ``detail``：预留的附加信息。

    借用 trader 的写法（``VtCoreServiceStatus``），供 ``serviceUtil`` 的两个注解读写。
    """

    name: str
    status: ServiceStatus = ServiceStatus.Idle
    fine: bool = True
    error: str | None = None
    detail: dict = field(default_factory=dict)

    def reset_to_status(self, status: ServiceStatus) -> None:
        """切换状态并清空上一轮的异常/详情信息。"""
        self.status = status
        self.fine = True
        self.error = None
        self.detail = {}
