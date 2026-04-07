from __future__ import annotations

from dataclasses import dataclass


@dataclass
class HistoryUsage:
    estimated_prompt_tokens: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    pre_check_triggered: bool = False
    overflow_retry: bool = False

    def to_json(self) -> dict:
        """仅输出显式有值的字段，保持存储 payload 简洁。"""
        result: dict = {}
        for key, value in self.__dict__.items():
            if value is not None:
                result[key] = value
        return result
