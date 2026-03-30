import json
import logging
from datetime import datetime
from enum import Enum
from typing import Any, TypeVar

import tornado.web
from playhouse.shortcuts import model_to_dict
from pydantic import BaseModel
from tornado.web import HTTPError

from model.dbModel.base import DbModelBase
from exception import TeamAgentException


logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class BaseHandler(tornado.web.RequestHandler):
    """所有 HTTP controller 的基类，提供统一的 JSON 响应方法。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.enhance = {}

    def _convert_gt_db(self, data: Any) -> Any:
        """将 DbModelBase 实例和其他对象转换为字典。"""
        if isinstance(data, Enum):
            return data.name
        if isinstance(data, datetime):
            return data.isoformat()
        if isinstance(data, DbModelBase):
            # 将 Peewee 模型转换为字典
            return self._convert_gt_db(model_to_dict(data))
        if hasattr(data, '__dict__'):
            # 通用对象转字典（过滤私有属性）
            result = {}
            for k, v in data.__dict__.items():
                if not k.startswith('_'):
                    result[k] = self._convert_gt_db(v)
            return result
        if isinstance(data, list):
            return [self._convert_gt_db(item) for item in data]
        if isinstance(data, dict):
            return {k: self._convert_gt_db(v) for k, v in data.items()}
        return data

    def parse_request(self, model_class: type[T]) -> T:
        """解析请求体为指定的 Pydantic 模型。"""
        body = json.loads(self.request.body)
        return model_class(**body)

    def return_json(self, data) -> None:
        """序列化并写入 JSON 响应。

        - Pydantic BaseModel：调用 model_dump(mode="json") 处理 datetime 等类型
        - DbModelBase：自动转换为字典
        - dict / list：直接 json.dumps
        """
        self.set_header("Content-Type", "application/json")
        if isinstance(data, BaseModel):
            self.write(data.model_dump(mode="json"))
        elif isinstance(data, (DbModelBase, list, dict)):
            converted = self._convert_gt_db(data)
            self.write(json.dumps(converted, ensure_ascii=False))
        else:
            self.write(json.dumps(data, ensure_ascii=False))

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
        if isinstance(value, TeamAgentException):
            # 自定义业务异常，不记录堆栈
            logger.warning(f"Business exception: {value.error_message}")
        else:
            # 其他异常，正常记录
            super().log_exception(typ, value, tb)

    def write_error(self, status_code, **kwargs) -> None:
        """写入错误响应"""
        logger.debug(f"write_error: status_code={status_code}, kwargs={kwargs}")

        exc_info = kwargs.get('exc_info')
        if exc_info and isinstance(exc_info[1], TeamAgentException):
            # 处理自定义异常
            exception_item: TeamAgentException = exc_info[1]
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

        ret_str = json.dumps(ret, ensure_ascii=False)
        self.write(ret_str)
