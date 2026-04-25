import json
import logging
from typing import Any, TypeVar

import tornado.web
from pydantic import BaseModel
from tornado.web import HTTPError

from exception import TogoException
from util import jsonUtil, configUtil


logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class BaseHandler(tornado.web.RequestHandler):
    """所有 HTTP controller 的基类，提供统一的 JSON 响应方法。"""

    _READONLY_BLOCKED_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.enhance = {}

    def prepare(self) -> None:
        """统一处理演示模式只读闸门。"""
        if self.request.method.upper() not in self._READONLY_BLOCKED_METHODS:
            return
        demo_mode = configUtil.get_app_config().setting.demo_mode
        if not demo_mode.read_only:
            return
        self.set_status(400)
        self.return_json(
            {
                "error_code": "demo_mode_data_frozen",
                "error_desc": "演示模式已冻结数据，当前操作不可用",
            }
        )
        raise tornado.web.Finish()

    def parse_request(self, model_class: type[T]) -> T:
        """解析请求体为指定的 Pydantic 模型。"""
        body = json.loads(self.request.body)
        return model_class(**body)

    def return_json(self, data, config: dict = None) -> None:
        """序列化并写入 JSON 响应。

        使用 jsonUtil.json_dump 处理 datetime、Enum、DbModelBase 等类型。
        DbModelBase 通过 to_json() 方法自动转换。
        """
        self.set_header("Content-Type", "application/json")
        if isinstance(data, BaseModel):
            # Pydantic 模型使用其内置序列化
            self.write(data.model_dump(mode="json"))
        else:
            # jsonUtil 会自动调用 DbModelBase.to_json()
            self.write(jsonUtil.json_dump(data, config=config))

    def return_success(self, **data) -> None:
        """返回统一成功响应。

        默认返回 {"status": "ok"}，可通过关键字参数追加字段。
        """
        payload = {"status": "ok"}
        payload.update(data)
        self.return_json(payload)

    def return_with_error(self, error_code: Any = None, error_desc: str = None) -> None:
        """抛出 HTTP 400 错误，并记录错误信息"""
        self.enhance['error_code'] = error_code
        self.enhance['error_desc'] = error_desc
        raise HTTPError(400)

    def log_exception(self, typ, value, tb) -> None:
        """处理异常日志"""
        if isinstance(value, TogoException):
            # 自定义业务异常，不记录堆栈
            logger.warning(f"Business exception: {value.error_message}")
        else:
            # 其他异常，正常记录
            super().log_exception(typ, value, tb)

    def write_error(self, status_code, **kwargs) -> None:
        """写入错误响应"""
        logger.debug(f"write_error: status_code={status_code}, kwargs={kwargs}")

        exc_info = kwargs.get('exc_info')
        if exc_info and isinstance(exc_info[1], TogoException):
            # 处理自定义异常
            exception_item: TogoException = exc_info[1]
            self.enhance['error_code'] = exception_item.error_code
            self.enhance['error_desc'] = exception_item.error_message
            status_code = 400
            self.set_status(400)

        # 所有错误都返回 JSON 格式
        self.set_header("Content-Type", "application/json")

        if status_code == 400:
            error_code = self.enhance.get('error_code')
            error_desc = self.enhance.get('error_desc')

            if error_code is None and exc_info and isinstance(exc_info[1], HTTPError):
                # 处理 Tornado HTTP 错误
                http_error: HTTPError = exc_info[1]
                error_desc = http_error.log_message

            ret = {
                "error_code": error_code,
                "error_desc": error_desc
            }
        else:
            # 其他状态码也返回 JSON，包含异常信息
            error_desc = "Internal Server Error"
            if exc_info:
                exc = exc_info[1]
                if isinstance(exc, Exception):
                    error_desc = str(exc)
            ret = {
                "error_code": None,
                "error_desc": error_desc
            }
            logger.error(f"Unhandled exception: {exc_info}")

        ret_str = jsonUtil.json_dump(ret)
        self.write(ret_str)
