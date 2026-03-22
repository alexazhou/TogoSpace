import json
import tornado.web
from pydantic import BaseModel


class BaseHandler(tornado.web.RequestHandler):
    """所有 HTTP controller 的基类，提供统一的 JSON 响应方法。"""

    def return_json(self, data) -> None:
        """序列化并写入 JSON 响应。

        - Pydantic BaseModel：调用 model_dump(mode="json") 处理 datetime 等类型
        - dict / list：直接 json.dumps
        """
        self.set_header("Content-Type", "application/json")
        if isinstance(data, BaseModel):
            self.write(data.model_dump(mode="json"))
        else:
            self.write(json.dumps(data, ensure_ascii=False))
