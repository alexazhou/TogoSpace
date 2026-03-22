import logging
import traceback
from typing import Any

from exception import TeamAgentException


logger = logging.getLogger(__name__)


class MakeSureException(TeamAgentException):
    """断言异常"""

    def __init__(self, error_message: str, error_code: Any = None):
        super().__init__(error_message, error_code)


def _log_caller() -> None:
    """记录调用者信息"""
    stack = traceback.extract_stack()
    if len(stack) >= 3:
        filename, lineno, func, _ = stack[-3]
        logger.info(f"makeSure failed, caller: {filename}:{lineno} in {func}")


def _get_arg_name(count: int) -> list[str]:
    """获取方法被调用时，传入的参数名称"""
    stack = traceback.extract_stack()
    if len(stack) < 3:
        return [f"arg{i}" for i in range(count)]

    filename, lineno, func, code = stack[-3]
    logger.debug(f"_get_arg_name {filename=} {lineno=} {func=} {code=}")

    try:
        # 提取参数部分
        _, params = code.strip().split("(", 1)
        if ")" in params:
            params, _ = params.rsplit(")", 1)

        param_names = params.split(",")
        return [p.strip() for p in param_names][:count]
    except Exception:
        return [f"arg{i}" for i in range(count)]


def assertTrue(a: bool, name: str | None = None, error_message: str | None = None, error_code: Any = None) -> None:
    """断言为 True"""
    if a is not True:
        if name is None:
            name = _get_arg_name(1)[0]

        _log_caller()
        if error_message is None:
            error_message = f"{name}[value={a}] must be True"

        raise MakeSureException(error_message, error_code=error_code)


def assertFalse(a: bool, name: str | None = None, error_message: str | None = None, error_code: Any = None) -> None:
    """断言为 False"""
    if a is not False:
        if name is None:
            name = _get_arg_name(1)[0]

        _log_caller()
        if error_message is None:
            error_message = f"{name}[value={a}] must be False"

        raise MakeSureException(error_message, error_code=error_code)


def assertEqual(a: Any, b: Any, error_message: str | None = None, error_code: Any = None) -> None:
    """断言相等"""
    if a != b:
        names = _get_arg_name(2)

        _log_caller()
        if error_message is None:
            error_message = f"must equal: {names[0]}[value={a}] and {names[1]}[value={b}]"

        raise MakeSureException(error_message, error_code=error_code)


def assertNotNull(a: Any, name: str | None = None, error_message: str | None = None, error_code: Any = None) -> None:
    """断言不为 None"""
    if a is None:
        if name is None:
            name = _get_arg_name(1)[0]

        _log_caller()
        if error_message is None:
            error_message = f"{name} must not be None"

        raise MakeSureException(error_message, error_code=error_code)


def assertNull(a: Any, name: str | None = None, error_message: str | None = None, error_code: Any = None) -> None:
    """断言为 None"""
    if a is not None:
        if name is None:
            name = _get_arg_name(1)[0]

        _log_caller()
        if error_message is None:
            error_message = f"{name} must be None"

        raise MakeSureException(error_message, error_code=error_code)