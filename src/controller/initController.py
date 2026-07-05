import logging
from typing import Any

from pydantic import BaseModel, ValidationError, field_validator

from constants import LlmProtocol, LlmProviderType
from controller.baseController import BaseHandler
from service import schedulerService
from util import configUtil
from util.configTypes import LlmProviderConfig, LlmModelConfig

logger = logging.getLogger(__name__)


class QuickInitRequest(BaseModel):
    """快速初始化请求体：必填三字段 + 可选类型。"""
    base_url: str
    api_key: str
    model: str
    type: LlmProviderType = LlmProviderType.OPENAI
    provider_params: dict[str, Any] | None = None

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("API 地址不能为空")
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("API 地址必须以 http:// 或 https:// 开头")
        return v

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("API Key 不能为空")
        return v

    @field_validator("model")
    @classmethod
    def validate_model(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("模型名称不能为空")
        return v


class QuickInitHandler(BaseHandler):
    """POST /config/quick_init.json — 快速初始化保存配置。"""

    async def post(self):
        try:
            req = self.parse_request(QuickInitRequest)
        except (ValidationError, Exception) as e:
            self.return_with_error(
                error_code="validation_error",
                error_desc=str(e),
            )
            return

        new_provider = LlmProviderConfig(
            name="default",
            urls={"openai": req.base_url},
            api_key=req.api_key,
            type=req.type,
            provider_params=req.provider_params or {},
            models=[
                LlmModelConfig(
                    name=req.model,
                    protocol=LlmProtocol.OPENAI,
                )
            ]
        )

        def mutator(s):
            # 若已存在名为 "default" 的 provider，先移除
            s.llm_providers = [p for p in s.llm_providers if p.name != "default"]
            s.llm_providers.append(new_provider)
            # 快速初始化时，只设置主模型，其他槽位留空
            s.default_models.primary = f"{req.model}@default"
            s.default_models.lite = ""
            s.default_models.vision = ""
            s.default_models.advanced = ""

        configUtil.update_setting(mutator)
        await schedulerService.start_schedule()

        self.return_json({
            "status": "ok",
            "message": "配置保存成功",
            "detail": {
                "name": "default",
                "model": req.model,
            },
        })
