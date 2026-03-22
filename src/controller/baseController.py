import json
from typing import Any
import tornado.web
from pydantic import BaseModel

from model.dbModel.base import DbModelBase


class BaseHandler(tornado.web.RequestHandler):
    """所有 HTTP controller 的基类，提供统一的 JSON 响应方法。"""

    def _convert_gt_db(self, data: Any) -> Any:
        """将 DbModelBase 实例转换为字典。"""
        if isinstance(data, DbModelBase):
            # 将 Peewee 模型转换为字典
            return data.__dict__.get('_data', {})
        if isinstance(data, list):
            return [self._convert_gt_db(item) for item in data]
        if isinstance(data, dict):
            return {k: self._convert_gt_db(v) for k, v in data.items()}
        return data

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
