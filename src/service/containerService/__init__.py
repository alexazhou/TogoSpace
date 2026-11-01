"""容器沙箱服务：Docker CLI 适配 + 容器生命周期编排。

对外统一从本包取接口（`from service import containerService` →
`containerService.create_container(...)`）。内部实现在 `core`（编排）与
`dockerCli`（CLI 适配）两个模块。

这里用 `from .core import *` 而不是只导出 `core` 模块名：调用方写
`containerService.create_container(...)` 比 `containerService.core.create_container(...)`
自然，且便于单测用 `monkeypatch.setattr(containerService, "create_container", ...)`
替换——若只导出模块，打桩就得打到 `containerService.core` 上，容易打错位置
（打桩打错会静默失效、测试仍然绿）。
"""
from .core import *  # noqa: F401,F403
from .core import (  # noqa: F401  显式导出，便于 IDE 与静态检查
    ERROR_CONTAINER_LOST,
    ERROR_CONTAINER_MISSING_BASH,
    ERROR_CONTAINER_NAME_CONFLICT,
    ContainerInfo,
    ContainerTarget,
    build_container_exec_argv,
    build_container_name,
    build_container_start_argv,
    build_create_argv,
    count_containers_by_agent_ids,
    create_container,
    delete_agent_containers,
    delete_container,
    delete_team_containers,
    get_cli,
    get_container_state,
    get_container_target,
    is_docker_available,
    is_short_id,
    list_agent_containers,
    prepare_container_gtsp,
    reset_cli_for_test,
    validate_enter_state,
)
