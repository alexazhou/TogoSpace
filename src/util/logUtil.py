import logging
import os
from logging.handlers import RotatingFileHandler
from typing import Dict, Optional

import appPaths

LOG_FORMAT = "%(asctime)s.%(msecs)03d - %(name)s - %(levelname)s - %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
MAX_BYTES = 100 * 1024 * 1024
BACKUP_COUNT = 3


# path: 拆分日志文件路径；global=False 表示不进入全局 backend.log/console
BACKEND_LOG_CONFIG: Dict[str, Dict[str, object]] = {
    "backend_main": {"path": "entry/backend_main.log", "global": True},
    "route": {"path": "entry/route.log", "global": True},
    "controller": {"path": "controller/controller.log", "global": False},
    "service.schedulerService": {"path": "service/schedulerService.log", "global": True},
    "service.roomService": {"path": "service/roomService.log", "global": True},
    "service.roleTemplateService": {"path": "service/roleTemplateService.log", "global": False},
    "service.agentService": {"path": "service/agentService.log", "global": True},
    "service.funcToolService": {"path": "service/funcToolService.log", "global": False},
    "service.messageBus": {"path": "service/messageBus.log", "global": False},
    "service.teamService": {"path": "service/teamService.log", "global": False},
    "service.containerService": {"path": "service/containerService.log", "global": False},
    "service.ormService": {"path": "service/ormService.log", "global": False},
    "service.persistenceService": {"path": "service/persistenceService.log", "global": False},
    "service.llmService": {"path": "service/llmService.log", "global": False},
    "util.llmApiUtil": {"path": "util/llm_api.log", "global": False},
    "util.assertUtil": {"path": "util/assert.log", "global": False},
    # 前端日志桥（#197）：浏览器/桌面前端 console 转发进来的日志单独落一个文件。
    # global=False —— 前端日志量大且多为噪声，混进 backend.log 会把真正的后端错误淹掉。
    # lazy=True —— **不在这里建文件**：`init_backend_logger` 会对每个带 path 的条目
    # 当场构造 handler，而 RotatingFileHandler 一构造就建文件（delay=False）。那样即使
    # 调试开关关着，启动后盘上也会凭空多出一个空的 frontend.log，违反「开关关闭时无该
    # 文件」。lazy 条目的 handler 由 `util.frontendLog` 在首条日志真正要落盘时自己挂。
    "util.frontendLog": {"path": "frontend/frontend.log", "global": False, "lazy": True},
    "dal": {"path": "dal/dal.log", "global": False},
    "LiteLLM": {"path": "litellm/litellm.log", "global": False},
}


def _match_config(logger_name: str) -> Optional[Dict[str, object]]:
    names = sorted(BACKEND_LOG_CONFIG.keys(), key=len, reverse=True)
    for name in names:
        if logger_name == name or logger_name.startswith(name + "."):
            return BACKEND_LOG_CONFIG[name]
    return None


def _build_global_filter():
    cache: Dict[str, bool] = {}

    def _filter(record: logging.LogRecord) -> bool:
        allow = cache.get(record.name)
        if allow is not None:
            return allow

        config = _match_config(record.name)
        allow = not (config is not None and config.get("global") is False)
        cache[record.name] = allow
        return allow

    return _filter


def _new_rotating_handler(path: str, formatter: logging.Formatter) -> RotatingFileHandler:
    handler = RotatingFileHandler(
        filename=path,
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(formatter)
    return handler


def ensure_lazy_handler(logger_name: str, log_dir: str | None = None) -> logging.Handler | None:
    """为首个 ``lazy=True`` 的 logger 挂上它的文件 handler（幂等）。

    `init_backend_logger` 会跳过 lazy 条目（见 `BACKEND_LOG_CONFIG` 注释），改由使用方
    在**确实要写第一条日志时**调用这里。这样「开关关着」时盘上不会出现空文件，而
    「开关打开」时也不需要用户手动重启或额外初始化 —— 第一条日志自己把文件建出来。

    返回挂上的 handler；logger 名不在配置里或配置没有 path 时返回 None。
    """
    config = _match_config(logger_name)
    if config is None:
        return None
    item_path = config.get("path")
    if not isinstance(item_path, str) or not item_path:
        return None

    target_logger = logging.getLogger(logger_name)
    if log_dir is None:
        log_dir = appPaths.LOGS_DIR

    total_path = os.path.join(log_dir, item_path)
    for handler in target_logger.handlers:
        if isinstance(handler, RotatingFileHandler) and getattr(handler, "baseFilename", None) == total_path:
            return handler

    os.makedirs(os.path.dirname(total_path), exist_ok=True)
    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)
    handler = _new_rotating_handler(total_path, formatter)
    target_logger.addHandler(handler)
    # addHandler 不改变 level：不设成 NOTSET 时继承了 root 的 INFO，debug 级别日志会被
    # 上层过滤掉，而前端日志里 debug 是正常诉求。
    if target_logger.level in (logging.NOTSET, logging.INFO):
        target_logger.setLevel(logging.DEBUG)
    return handler


def init_backend_logger(log_dir: str | None = None) -> None:
    """初始化后端日志：
    1) 按 logger 前缀拆分到不同文件；
    2) 保留全局 backend.log 与 backend_warning.log；
    3) 控制台输出与全局日志使用相同过滤规则。
    """

    if log_dir is None:
        log_dir = appPaths.LOGS_DIR
    os.makedirs(log_dir, exist_ok=True)

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)
    global_filter = _build_global_filter()
    created_handlers: Dict[str, logging.Handler] = {}

    for logger_name, config in BACKEND_LOG_CONFIG.items():
        item_logger = logging.getLogger(logger_name)
        item_logger.handlers.clear()

        item_level = config.get("level")
        if isinstance(item_level, int):
            item_logger.setLevel(item_level)

        item_path = config.get("path")
        if config.get("lazy") is True:
            # 懒加载：不建文件、不挂 handler，留给使用方在首条日志时调
            # `ensure_lazy_handler()`。见 `BACKEND_LOG_CONFIG` 里该条目的注释。
            continue
        if isinstance(item_path, str) and item_path:
            total_path = os.path.join(log_dir, item_path)
            os.makedirs(os.path.dirname(total_path), exist_ok=True)

            handler = created_handlers.get(total_path)
            if handler is None:
                handler = _new_rotating_handler(total_path, formatter)
                created_handlers[total_path] = handler
            item_logger.addHandler(handler)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(logging.INFO)

    global_handler = _new_rotating_handler(os.path.join(log_dir, "backend.log"), formatter)
    global_handler.addFilter(global_filter)

    warn_handler = _new_rotating_handler(os.path.join(log_dir, "backend_warning.log"), formatter)
    warn_handler.setLevel(logging.WARNING)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.addFilter(global_filter)

    root_logger.addHandler(global_handler)
    root_logger.addHandler(warn_handler)
    root_logger.addHandler(console_handler)
