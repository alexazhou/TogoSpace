import json
import logging
from typing import Any, TypeVar

import tornado.web
from pydantic import BaseModel
from tornado.web import HTTPError

from constants import DESKTOP_ALLOWED_ORIGINS, DEVELOPMENT_ALLOWED_ORIGINS
from exception import TogoException
from util import jsonUtil, configUtil


logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


def allowed_web_origins() -> set[str]:
    """返回允许跨源访问后端的 Origin 白名单。

    桌面形态的页面源是 `tauri://localhost` / `http://tauri.localhost`；
    开发模式下额外放行 Vite devUrl（`tauri dev` 窗口与后端不同源）。
    """
    origins = set(DESKTOP_ALLOWED_ORIGINS)
    try:
        if configUtil.get_app_config().setting.development_mode:
            origins.update(DEVELOPMENT_ALLOWED_ORIGINS)
    except Exception:
        # 配置尚未加载时仅保留桌面白名单，避免影响启动早期请求
        pass
    return origins


class BaseHandler(tornado.web.RequestHandler):
    """所有 HTTP controller 的基类，提供统一的 JSON 响应方法。"""

    _READONLY_BLOCKED_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
    _AUTH_EXEMPT_PATHS = {
        "/system/status.json",  # 系统状态接口需豁免，用于前端判断是否需要输入 token
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.enhance = {}

    def set_default_headers(self) -> None:
        """按白名单回填 CORS 响应头。

        只回显命中白名单的 Origin，不使用 `*`；浏览器形态同源请求不受影响。
        """
        origin = self.request.headers.get("Origin")
        if origin and origin in allowed_web_origins():
            self.set_header("Access-Control-Allow-Origin", origin)
            self.set_header("Vary", "Origin")
            self.set_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.set_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")

    def options(self, *args, **kwargs) -> None:
        """CORS 预检统一返回 204（响应头由 set_default_headers 回填）。"""
        self.set_status(204)
        self.finish()

    def prepare(self) -> None:
        """统一处理演示模式只读闸门和鉴权检查。"""
        # 预检请求不涉及业务操作，直接放行
        if self.request.method.upper() == "OPTIONS":
            return

        # 1. 演示模式检查
        if self.request.method.upper() in self._READONLY_BLOCKED_METHODS:
            demo_mode = configUtil.get_app_config().setting.demo_mode
            if demo_mode.read_only:
                self.set_status(400)
                self.return_json(
                    {
                        "error_code": "demo_mode_data_frozen",
                        "error_desc": "演示模式已冻结数据，当前操作不可用",
                    }
                )
                raise tornado.web.Finish()

        # 2. 鉴权检查
        self._check_auth()

    def _check_auth(self) -> None:
        """检查请求是否携带正确 token。"""
        auth_config = configUtil.get_app_config().setting.auth

        # 鉴权未启用，跳过检查
        if not auth_config.enabled:
            return

        # 豁免路径，跳过检查
        if self.request.path in self._AUTH_EXEMPT_PATHS:
            return

        # 获取 token（仅支持 Authorization header）
        auth_header = self.request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            self.set_status(401)
            self.return_json({"error_code": "auth_required", "error_desc": "请输入访问 Token"})
            raise tornado.web.Finish()

        token = auth_header[7:]

        # 验证 token
        if token != auth_config.token:
            self.set_status(401)
            self.return_json({"error_code": "auth_invalid", "error_desc": "Token 无效"})
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
