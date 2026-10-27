from typing import Any


class TogoException(Exception):
    """自定义异常类，用于业务异常处理"""

    def __init__(self, error_message: str, error_code: Any = None):
        # 消息必须是 str：`__str__` 直接返回它，传 None 会让 `str(exc)` 抛
        # `TypeError: __str__ returned non-string (type NoneType)`，把真正的业务错误
        # 盖成一个更难查的解释器错误。在构造点拦住，堆栈直接指向写错的那一处。
        assert isinstance(error_message, str), (
            f"TogoException.error_message 必须是 str（当前 {type(error_message).__name__}）"
        )
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
        message = self._error_message
        # `__init__` 的 assert 已保证是 str；这里再兜一层，防两种绕过：
        # `python -O`（assert 被剥离）与子类不调 super().__init__ 直接赋值。
        # 否则 `__str__` 抛 `TypeError: __str__ returned non-string`，
        # 会把真正的业务错误盖成一个更难查的解释器错误。
        return message if isinstance(message, str) else str(message)
