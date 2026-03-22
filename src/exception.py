from typing import Any


class TeamAgentException(Exception):
    """自定义异常类，用于业务异常处理"""

    def __init__(self, error_message: str, error_code: Any = None):
        super().__init__(error_message, error_code)
        self._error_message = error_message
        self._error_code = error_code

    @property
    def error_message(self) -> str:
        return self._error_message

    @property
    def error_code(self) -> Any:
        return self._error_code