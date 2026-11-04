import atexit
import logging
import os
import queue
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from typing import Dict, List, Optional

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
    "dal": {"path": "dal/dal.log", "global": False},
    "LiteLLM": {"path": "litellm/litellm.log", "global": False},
}


# 异步化运行态：listener 与真实 handler 由模块级持有，供幂等重入和进程退出时收尾。
_listeners: List[QueueListener] = []
_owned_handlers: List[logging.Handler] = []


def _stop_listeners() -> None:
    """逆序停止 listener（先建的后停）。QueueListener.stop() 先投哨兵再 join，
    因此队列里已入队的记录会在停止前被消费完。"""
    while _listeners:
        _listeners.pop().stop()


def _shutdown_async_logging() -> None:
    """atexit 钩子：排空队列。

    logging.shutdown 在 `import logging` 时就已注册（早于本模块），atexit 是 LIFO，
    所以本函数必然先执行：先排空队列 → 再由 logging.shutdown flush/close 真实 handler。
    不要在这里再调一次 logging.shutdown()。
    """
    _stop_listeners()


atexit.register(_shutdown_async_logging)


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


class _FileRouteHandler(logging.Handler):
    """拆分文件队列的消费端：按记录来源 logger 查出配置 path，查表转发给对应真实 handler。

    真实 handler 仍在 init（主线程）时创建，文件创建/切分行为与同步实现一致；
    listener 线程只负责 emit。18 个拆分文件因此只需要 1 条队列 + 1 个路由 handler，
    而不是按文件各起一条队列、各开一个守护线程。
    """

    def __init__(
        self,
        handlers_by_path: Dict[str, logging.Handler],
        fallback_handler: logging.Handler,
    ) -> None:
        super().__init__()
        self._handlers_by_path = handlers_by_path
        self._fallback_handler = fallback_handler

    def emit(self, record: logging.LogRecord) -> None:
        config = _match_config(record.name)
        config_path = config.get("path") if config is not None else None
        handler = self._handlers_by_path.get(config_path) if isinstance(config_path, str) else None
        if handler is None:
            # 兜底：进本队列的记录理论上必然来自某个 item_logger 或其子 logger，查不到配置
            # 说明配置与挂载不一致。宁可多写一条进 global，也不静默丢日志。
            handler = self._fallback_handler
        handler.handle(record)


def init_backend_logger(log_dir: str | None = None) -> None:
    """初始化后端日志（异步写盘）：
    1) 按 logger 前缀拆分到不同文件；
    2) 保留全局 backend.log 与 backend_warning.log；
    3) 控制台输出与全局日志使用相同过滤规则；
    4) 主线程写日志只做内存入队，格式化与落盘由后台 listener 线程完成。

    重复调用是幂等的：先停旧 listener（排空旧队列）并关闭旧 handler，再重建，
    避免漏 fd 与双写。
    """

    _stop_listeners()
    for old_handler in _owned_handlers:
        old_handler.close()
    _owned_handlers.clear()

    if log_dir is None:
        log_dir = appPaths.LOGS_DIR
    os.makedirs(log_dir, exist_ok=True)

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)
    global_filter = _build_global_filter()
    created_handlers: Dict[str, logging.Handler] = {}
    # 配置里的相对 path -> 真实 handler，供 _FileRouteHandler 查表（record.name -> config.path -> handler）
    routed_handlers: Dict[str, logging.Handler] = {}

    file_queue: "queue.SimpleQueue[logging.LogRecord]" = queue.SimpleQueue()

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
                _owned_handlers.append(handler)
            routed_handlers[item_path] = handler
            # 主线程侧只挂队列；真实 handler 交给 listener 线程按 path 转发
            item_logger.addHandler(QueueHandler(file_queue))

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

    _owned_handlers.extend([global_handler, warn_handler, console_handler])

    global_queue: "queue.SimpleQueue[logging.LogRecord]" = queue.SimpleQueue()
    root_logger.addHandler(QueueHandler(global_queue))

    # 两条队列分别对齐「item handler 一级 / root handler 一级」：
    # item_logger 的 handler 与 root 的 handler 是两级（propagate 默认 True），
    # 共用一条队列会让同一条记录被 enqueue 两次、拆分文件写两遍。
    # respect_handler_level=True 必须传：默认 False 会绕过 handler 的 level 判断，
    # 让 INFO 灌进 backend_warning.log。filter 留在真实 handler 上，语义就是「该不该进这个目的地」。
    file_listener = QueueListener(
        file_queue,
        _FileRouteHandler(routed_handlers, global_handler),
        respect_handler_level=True,
    )
    global_listener = QueueListener(
        global_queue,
        global_handler,
        warn_handler,
        console_handler,
        respect_handler_level=True,
    )
    file_listener.start()
    global_listener.start()
    _listeners.extend([file_listener, global_listener])
