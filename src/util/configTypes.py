from typing_extensions import NotRequired, Required, TypedDict


class TeamRoomConfig(TypedDict, total=False):
    """Single room item in team config."""

    id: int
    name: Required[str]
    members: Required[list[str]]
    initial_topic: str
    max_turns: int


class TeamConfig(TypedDict, total=False):
    """Canonical team config shape loaded from JSON/DB."""

    name: Required[str]
    members: Required[list[str]]
    preset_rooms: Required[list[TeamRoomConfig]]
    max_function_calls: NotRequired[int]


class TeamConfigPatch(TypedDict, total=False):
    """Update payload shape for partial team updates."""

    name: Required[str]
    members: list[str]
    preset_rooms: list[TeamRoomConfig]
    max_function_calls: int


__all__ = ["TeamRoomConfig", "TeamConfig", "TeamConfigPatch"]
