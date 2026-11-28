"""接收前端 console 日志，写进 `{STORAGE_ROOT}/logs/backend/frontend/frontend.log`。

开关关着的机器上会凭空多出一个空的 `frontend.log`，所以建文件只能发生在「开关确认打开、且确实有一条要写」之后：`RotatingFileHandler` 一构造就会建文件，不能在 `init_backend_logger` 阶段就挂。前端的开关只用来省流量，真正决定写不写盘的是后端的 `TOGOSPACE_FRONTEND_DEBUG`——只信前端自觉的话，任何能连上 ws 的客户端都能往磁盘写日志。

改这里时保持 `logUtil` 里那条 `lazy=True`，改完跑 `tests/unit/util/test_frontend_log.py`；两道开关、单条截断与时间戳见 `docs/tech/08_debugging/frontend_log_bridge.md`。"""

from __future__ import annotations

import contextlib
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

from util import logUtil

ENV_FRONTEND_DEBUG = "TOGOSPACE_FRONTEND_DEBUG"

#: 单条日志正文的最大字符数。前端已有 4000 的截断，这里是**后端**的兜底（不信任客户端）。
MAX_MESSAGE_CHARS = 8000

#: 允许的级别。白名单而非透传 —— 级别会进日志行，透传等于让客户端控制日志格式。
ALLOWED_LEVELS = frozenset({"log", "info", "warn", "error", "debug"})

_LEVEL_TO_LOG_LEVEL = {
    "log": logging.INFO,
    "info": logging.INFO,
    "debug": logging.DEBUG,
    "warn": logging.WARNING,
    "error": logging.ERROR,
}

#: 控制字符（含换行）会伪造日志行 / 注入假时间戳，必须剔除。
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_logger = logging.getLogger(__name__)

#: 本模块的 handler 是否已就绪。见 `logUtil.BACKEND_LOG_CONFIG["util.frontendLog"]` 的
#: `lazy` 说明：文件**不在后端启动时创建**，而是第一条真正要落盘的日志到来时才建。
_sink_ready = False


def _ensure_sink() -> None:
    """确保写盘 handler 已挂上（幂等）。

    刻意不放在模块导入时做：`init_backend_logger` 尚未运行时 `LOGS_DIR` 可能还不存在，
    更关键的是那样会让「开关关着也建文件」。这里只在**确认要写第一条**时调用。

    判据里带上 `_logger.handlers`：`init_backend_logger` 会对手上每个 logger 做
    `handlers.clear()`。若它在本模块首次落盘之后才被调用（测试进程里就可能这样），
    只信布尔标记会让此后所有日志静默消失 —— 那种「开关开着、文件也在、就是不涨」
    最难查。
    """
    global _sink_ready
    if _sink_ready and _logger.handlers:
        return
    logUtil.ensure_lazy_handler(__name__)
    _sink_ready = True


def _reset_sink_for_tests() -> None:
    """仅供测试：摘掉本模块的文件 handler，让下一次写盘按当前 LOGS_DIR 重建。"""
    global _sink_ready
    for handler in list(_logger.handlers):
        _logger.removeHandler(handler)
        with contextlib.suppress(Exception):
            handler.close()
    _sink_ready = False


def frontend_debug_enabled(env: dict[str, str] | None = None) -> bool:
    """读取调试开关。取值口径与 Tauri 外壳 `frontend_debug_enabled()` 保持一致。

    外壳侧接受 `1` / `true`（大小写不敏感，允许前后空白）；这里保持同一口径，
    避免「外壳开了、后端没开」这种半开的诡异状态。
    """
    values = os.environ if env is None else env
    raw = values.get(ENV_FRONTEND_DEBUG, "")
    value = raw.strip()
    return value == "1" or value.lower() == "true"


def _sanitize(text: str) -> str:
    """去掉控制字符并截断到 `MAX_MESSAGE_CHARS`。

    换行必须去掉：日志是行导向的，正文里带换行等于让客户端往文件里插任意多行
    （连假的 `2026-01-01 ... ERROR` 行都能造出来）。
    """
    cleaned = _CONTROL_CHARS.sub("", text.replace("\r", "").replace("\n", "\\n"))
    if len(cleaned) <= MAX_MESSAGE_CHARS:
        return cleaned
    dropped = len(cleaned) - MAX_MESSAGE_CHARS
    return f"{cleaned[:MAX_MESSAGE_CHARS]}… (truncated {dropped} chars)"


def _format_frontend_ts(raw: Any) -> str:
    """把前端回传的毫秒时间戳格式化成可读串；非法/缺失返回 `-`。

    只做展示，不参与排序 —— 客户端时钟不可信。
    """
    if not isinstance(raw, (int, float)) or isinstance(raw, bool):
        return "-"
    try:
        moment = datetime.fromtimestamp(float(raw) / 1000.0, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return "-"
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def handle_message(payload: dict[str, Any], *, client: str = "-") -> bool:
    """处理一条上行 `frontend_log` 报文。

    返回是否真的写了盘。**任何非法输入都只是不写，不抛异常** —— 这条链路在 ws
    消息回调里，抛异常会连带影响正常的业务事件推送。

    参数:
        payload: 前端报文，期望 `{"type": "frontend_log", "level": ..., "message": ..., "ts": ...}`
        client: 连接标识（如远端地址），写进日志便于多窗口区分。
    """
    if not frontend_debug_enabled():
        return False

    level = payload.get("level")
    if level not in ALLOWED_LEVELS:
        level = "log"

    message = payload.get("message")
    if not isinstance(message, str):
        # 允许前端把非字符串（对象/数组）序列化后放进 `message`；这里只接受字符串，
        # 其它类型直接丢弃而不是 str() 一把 —— str(dict) 出来的是 Python repr，
        # 不是前端看到的形状。
        return False

    # 纯空白/纯换行在清洗后会变成一串 `\n` 转义符，看着非空、实际零信息，
    # 只会在盘上刷出无意义的行。判空要在**清洗前**按「有无可见字符」判。
    if not message.strip():
        return False

    message = _sanitize(message)
    if not message:
        return False

    _ensure_sink()
    _logger.log(
        _LEVEL_TO_LOG_LEVEL.get(level, logging.INFO),
        "[frontend] level=%s client=%s frontend_ts=%s %s",
        level,
        client,
        _format_frontend_ts(payload.get("ts")),
        message,
    )
    return True
