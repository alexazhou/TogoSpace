import logging
import os
from logging.handlers import RotatingFileHandler
from typing import Dict, Optional

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
    "service.agentService": {"path": "service/agentService.log", "global": True},
    "service.funcToolService": {"path": "service/funcToolService.log", "global": False},
    "service.messageBus": {"path": "service/messageBus.log", "global": False},
    "service.teamService": {"path": "service/teamService.log", "global": False},
    "service.ormService": {"path": "service/ormService.log", "global": False},
    "service.persistenceService": {"path": "service/persistenceService.log", "global": False},
    "service.llmService": {"path": "service/llmService.log", "global": False},
    "util.llmApiUtil": {"path": "util/llm_api.log", "global": False},
    "util.assertUtil": {"path": "util/assert.log", "global": False},
    "dal": {"path": "dal/dal.log", "global": False},
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


def init_backend_logger(log_dir: str | None = None) -> None:
    """初始化后端日志：
    1) 按 logger 前缀拆分到不同文件；
    2) 保留全局 backend.log 与 backend_warning.log；
    3) 控制台输出与全局日志使用相同过滤规则。
    """

    if log_dir is None:
        log_dir = os.path.join(os.getcwd(), "../logs/backend")
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
