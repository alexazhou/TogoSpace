"""前端日志桥（#197）：把前端 console 日志收下来写进后端本地文件。

## 为什么需要

前端（桌面端 / 浏览器）运行时的 log 拿不到、无法落库，排查前端问题只能靠人肉看
devtools。本模块让后端把前端日志落成本地文件，事后可取。

## 开关（两道，缺一不可）

1. **环境变量 `TOGOSPACE_FRONTEND_DEBUG`**（与 Tauri 外壳注入 `window.tgconfig.frontendDebug`
   用的是同一个变量）：**后端**据此决定是否真的写盘。`/system/status.json` 也把它暴露成
   `frontend_debug` 字段，前端据此决定是否发送。
2. 前端侧还有自己的判断（`desktopLog.ts` 的 `debugForwardingEnabled()`）。

**为什么后端必须再判一次**：只靠前端自觉等于没有门控 —— 任何能连上 ws 的客户端都能
往盘上写。后端这一道是真正的闸门，前端那道只是省流量。

## 写什么、不写什么

- `warn` / `error` **和** `log` 都接收（开关打开时全量才有排查价值），级别由前端报文带上来。
- 单条消息在**后端**再截断一次（`MAX_MESSAGE_CHARS`）：不信任客户端，防止一条超长日志
  把文件撑爆。
- 单条长度、总量上限由既有 `RotatingFileHandler`（100MB × 3）承担，不新造轮子。

## 文件何时出现

**首条日志落盘时才创建**（`logUtil` 里该条目带 `lazy=True`，不在
`init_backend_logger` 阶段建文件）。原因很直接：`RotatingFileHandler` 一构造就建
文件，若在启动时就挂上，开关关着的机器上也会凭空多出一个空 `frontend.log` ——
而交付标准要求「开关关闭时无该文件」。所以建文件这件事只能发生在
「开关已确认为开 + 确实有一条要写」之后。

## 时间戳

前端报文可以带 `ts`（浏览器侧 `Date.now()`），写盘时**两者都留**：后端收到的时间
（writer 的 `asctime`）用来对齐后端其他日志，前端时间用来判断「是前端卡住了还是后端
收晚了」——这类差异恰恰是排查白屏/卡顿的关键，只留一个就看不出来了。
"""

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
