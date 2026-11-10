from typing import Any, Callable, Optional


class ForwardToolCall(Exception):
    """「把这次调用转发给另一个工具」的信号（#160）。

    为什么用异常而不是返回值：`console` 的每条子命令都等价于一个原工具（`save_agent`
    / `get_dept_info` …），而**权限与执行只有一份实现**才是本单的目标。若 console 自己
    再算一遍权限（`_assert_permitted`），就必然出现「原工具调不了、console 却能调」这类
    错位。改成抛这个信号后，console 只负责「命令 → 目标工具 + 参数」，真正的鉴权与执行
    全部回到唯一入口 `AgentToolRegistry.execute_tool_call`。

    为什么放在 `src/exception.py` 这种叶子模块：`toolRegistry` 要 import 它，而
    `funcToolService/__init__.py` 会 `from .core import *` → `core` → `tools.py` →
    `toolRegistry`。若把本异常放进 `funcToolService` 包内（任务单的建议落点），
    `toolRegistry` 顶层 import 它就会触发上面这条链的**循环导入**
    （`ImportError: cannot import name 'validate_tool_allow_specs' ... partially initialized`）。
    `exception.py` 只依赖 `typing`，是叶子模块，任何模块都能安全 import。

    Attributes:
        tool_name: 转发目标工具名（必须是注册表里真实存在的名字）。
        tool_args: 目标工具的关键字参数字典（**不要**包含 `_context`：它由
            `run_tool_call` 按当前上下文注入，签名里有 `_context` 才会注入）。
            **不能**叫 `args`：`BaseException.args` 是内置描述符，给它赋 dict 会被
            强制转成 tuple（拿到的是**键的元组**），随后 `json.dumps` 出去变成 list，
            `func(**list)` 直接 `TypeError: argument after ** must be a mapping`。
            实测踩过这个坑，别改回 `args`。
        post_process: 可选的返回值后处理回调。仅用于「依赖目标工具返回体」的本地逻辑
            （例如 `rm` 把 `impact` 并回结果、`agent get` 的 id 一致性守卫）；
            **不得**在其中做权限判定——鉴权已经执行完了。
    """

    def __init__(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        *,
        post_process: Optional[Callable[[dict[str, Any]], dict[str, Any]]] = None,
    ) -> None:
        # 只把人类可读的一句话交给 BaseException.args，转发参数走独立属性
        # （`tool_args` 而不是 `args`，原因见类 docstring）。
        super().__init__(f"forward to {tool_name}")
        self.tool_name = tool_name
        self.tool_args = tool_args
        self.post_process = post_process


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
