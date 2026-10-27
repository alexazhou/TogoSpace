from typing import Any


class TogoException(Exception):
    """自定义异常类，用于业务异常处理"""

    def __init__(self, error_message: str, error_code: Any = None):
        # 刻意保留双参数 args：`repr` / `args` 仍能看到 error_code，便于日志排查。
        # 「给人读的文本」由下面的 __str__ 单独提供，不再靠 args 承担。
        super().__init__(error_message, error_code)
        self._error_message = error_message
        self._error_code = error_code

    @property
    def error_message(self) -> str:
        return self._error_message

    @property
    def error_code(self) -> Any:
        return self._error_code

    def __str__(self) -> str:
        """只返回业务错误消息本身，不带 error_code。

        基类 `Exception.__str__` 对**两个及以上** `args` 会返回整个元组的 `repr()`，
        于是 `str(exc)` 变成 `('已存在成员相同的房间', 'ROOM_DUPLICATE')` —— 这串东西会被
        原样塞进工具层返回给模型的消息、以及 REST 的 `error_desc` 里。
        错误码请走 `error_code` 属性（结构化字段），不要混进给人读的文本。
        """
        return self._error_message
