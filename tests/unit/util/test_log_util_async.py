"""后端日志异步化（#131）单测。

覆盖范围（对应任务规格 §4）：
1. 拆分文件各进各的；
2. global=False 的 logger 不进 backend.log / console；
3. warn_handler 的 WARNING 门槛不被绕过；
4. stop 后队列内 N 条日志全部落盘；
5. logger.exception 保留 Traceback；
6. 一行日志只有一个时间戳前缀（防「QueueHandler 也设 formatter」回归）。

约定：日志目录用 tmp_path；每个用例结束都停 listener（等价 atexit 行为）并恢复全局
logging 状态，不污染其他用例的 root handlers。
"""

import contextlib
import io
import logging
import re
from typing import Iterator, Tuple

import pytest

from util import logUtil

TIMESTAMP_PREFIX = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}")


@pytest.fixture()
def backend_logs(tmp_path) -> Iterator[Tuple[object, io.StringIO]]:
    """把后端日志初始化到 tmp_path，并捕获 console handler 写入的 stderr。"""

    root_logger = logging.getLogger()
    saved_root_handlers = root_logger.handlers[:]
    saved_root_level = root_logger.level
    saved_item_state = {}
    for name in logUtil.BACKEND_LOG_CONFIG:
        item_logger = logging.getLogger(name)
        saved_item_state[name] = (item_logger.handlers[:], item_logger.level)

    stderr_buffer = io.StringIO()
    with contextlib.redirect_stderr(stderr_buffer):
        logUtil.init_backend_logger(str(tmp_path))

    try:
        yield tmp_path, stderr_buffer
    finally:
        # 等价于 atexit 的收尾：先排空队列，再关掉真实 handler
        logUtil._stop_listeners()
        for handler in logUtil._owned_handlers:
            handler.close()
        logUtil._owned_handlers.clear()

        root_logger.handlers[:] = saved_root_handlers
        root_logger.setLevel(saved_root_level)
        for name, (handlers, level) in saved_item_state.items():
            item_logger = logging.getLogger(name)
            item_logger.handlers[:] = handlers
            item_logger.setLevel(level)


def _read(path) -> str:
    return path.read_text(encoding="utf-8")


def test_split_files_are_isolated(backend_logs) -> None:
    """① 每个 logger 的记录只进自己的拆分文件。"""
    log_dir, _ = backend_logs

    logging.getLogger("service.roomService").info("room-msg")
    logging.getLogger("dal").info("dal-msg")
    logUtil._stop_listeners()

    room_log = _read(log_dir / "service/roomService.log")
    dal_log = _read(log_dir / "dal/dal.log")

    assert "room-msg" in room_log
    assert "dal-msg" not in room_log
    assert "dal-msg" in dal_log
    assert "room-msg" not in dal_log


def test_global_false_logger_excluded_from_backend_and_console(backend_logs) -> None:
    """② global=False（controller）不进 backend.log，也不进 console。"""
    log_dir, stderr_buffer = backend_logs

    logging.getLogger("controller").info("controller-msg")
    logUtil._stop_listeners()

    assert "controller-msg" in _read(log_dir / "controller/controller.log")
    assert "controller-msg" not in _read(log_dir / "backend.log")
    assert "controller-msg" not in stderr_buffer.getvalue()


def test_global_true_logger_reaches_backend_and_console(backend_logs) -> None:
    """② 的对照：global=True 的 logger 同时进 backend.log 与 console。"""
    log_dir, stderr_buffer = backend_logs

    logging.getLogger("service.roomService").info("global-msg")
    logUtil._stop_listeners()

    assert "global-msg" in _read(log_dir / "backend.log")
    assert "global-msg" in stderr_buffer.getvalue()


def test_warning_level_threshold_not_bypassed(backend_logs) -> None:
    """③ INFO 不得灌进 backend_warning.log；WARNING 同时出现在 backend.log 与 warning 文件。"""
    log_dir, _ = backend_logs
    item_logger = logging.getLogger("service.roomService")

    item_logger.info("info-only")
    item_logger.warning("warn-msg")
    logUtil._stop_listeners()

    warning_log = _read(log_dir / "backend_warning.log")
    backend_log = _read(log_dir / "backend.log")

    assert "info-only" not in warning_log
    assert "warn-msg" in warning_log
    # WARNING 同时出现在两个文件是现状既有行为，不在这里「修」
    assert "warn-msg" in backend_log
    assert "info-only" in backend_log


def test_stop_flushes_all_queued_records(backend_logs) -> None:
    """④ stop 后队列里积压的 N 条日志全部落盘。"""
    log_dir, _ = backend_logs
    item_logger = logging.getLogger("service.roomService")

    total = 500
    for index in range(total):
        item_logger.info("flush-%d", index)
    logUtil._stop_listeners()

    content = _read(log_dir / "service/roomService.log")
    for index in range(total):
        assert "flush-%d -" % index in content or "flush-%d\n" % index in content
    assert content.count("flush-") == total


def test_exception_keeps_traceback(backend_logs) -> None:
    """⑤ logger.exception 落盘后仍有 Traceback。"""
    log_dir, _ = backend_logs

    try:
        raise ValueError("boom")
    except ValueError:
        logging.getLogger("service.roomService").exception("exc-msg")
    logUtil._stop_listeners()

    content = _read(log_dir / "service/roomService.log")
    assert "exc-msg" in content
    assert "Traceback (most recent call last)" in content
    assert "ValueError: boom" in content


def test_single_timestamp_prefix_per_line(backend_logs) -> None:
    """⑥ 一行日志只有一个时间戳前缀（QueueHandler 设了 formatter 会重复两遍）。"""
    log_dir, stderr_buffer = backend_logs

    logging.getLogger("service.roomService").info("once-msg")
    logUtil._stop_listeners()

    file_lines = [line for line in _read(log_dir / "service/roomService.log").splitlines() if "once-msg" in line]
    assert len(file_lines) == 1
    assert len(TIMESTAMP_PREFIX.findall(file_lines[0])) == 1, file_lines[0]
    assert file_lines[0].endswith("- INFO - once-msg"), file_lines[0]

    console_lines = [line for line in stderr_buffer.getvalue().splitlines() if "once-msg" in line]
    assert len(console_lines) == 1
    assert len(TIMESTAMP_PREFIX.findall(console_lines[0])) == 1, console_lines[0]
