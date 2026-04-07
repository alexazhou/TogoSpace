from __future__ import annotations

from dataclasses import dataclass

CompactStage = str
_VALID_COMPACT_STAGES = {"none", "pre", "post"}


@dataclass
class HistoryUsage:
    estimated_prompt_tokens: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    compact_stage: CompactStage = "none"
    overflow_retry: bool = False

    def __post_init__(self) -> None:
        if self.compact_stage not in _VALID_COMPACT_STAGES:
            raise ValueError(f"invalid compact_stage: {self.compact_stage}")

    def to_json(self) -> dict:
        """仅输出显式有值的字段，保持存储 payload 简洁。"""
        result: dict = {}
        for key, value in self.__dict__.items():
            if value is not None:
                result[key] = value
        return result
