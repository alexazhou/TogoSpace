from __future__ import annotations

from typing import Any, Self

from pydantic import BaseModel


class ThirdPartySearchResult(BaseModel):
    """三方搜索服务、平台接口与搜索工具共用的结果。"""

    success: bool
    service: str
    duration_ms: int = 0
    content: str | None = None
    sources: list[dict[str, Any]] | None = None
    error_message: str | None = None

    @classmethod
    def failure(cls, service: str, error_message: str, duration_ms: int = 0) -> Self:
        """构造失败结果。"""
        return cls(
            success=False,
            service=service,
            error_message=error_message,
            duration_ms=duration_ms,
        )
