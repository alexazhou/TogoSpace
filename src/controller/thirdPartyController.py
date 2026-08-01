import json
import logging

from pydantic import BaseModel, ValidationError

from controller.baseController import BaseHandler
from service.thirdPartyService import deepseekService, xiaomiMimoService
from util import configUtil, jsonUtil
from util.configTypes import ThirdPartyServicesConfig

logger = logging.getLogger(__name__)


class ThirdPartyServicesPayload(BaseModel):
    third_party_services: ThirdPartyServicesConfig


class DeepSeekSearchTestRequest(BaseModel):
    enabled: bool = True
    api_key: str = ""
    query: str


class XiaomiMiMoSearchTestRequest(BaseModel):
    enabled: bool = True
    api_key: str = ""
    query: str


class ThirdPartyServicesConfigHandler(BaseHandler):
    """GET/POST /config/third_party_services.json"""

    async def get(self) -> None:
        setting = configUtil.get_app_config().setting
        services = setting.third_party_services.model_dump(mode="json")
        if setting.demo_mode.hide_sensitive:
            services["deepseek"]["api_key"] = ""
            services["xiaomi_mimo"]["api_key"] = ""

        self.return_json({
            "third_party_services": services,
        })

    async def post(self) -> None:
        try:
            body = json.loads(self.request.body)
            services_data = jsonUtil.clean_null_values(body.get("third_party_services", {}))
            services = ThirdPartyServicesConfig.model_validate(services_data)
        except (json.JSONDecodeError, ValidationError) as e:
            self.return_with_error(error_code="validation_error", error_desc=str(e))
            return

        for service_name, service_config in (
            ("DeepSeek", services.deepseek),
            ("Xiaomi MiMo", services.xiaomi_mimo),
        ):
            if service_config.enabled and not service_config.api_key.strip():
                self.return_with_error(
                    error_code="validation_error",
                    error_desc=f"启用 {service_name} 搜索服务时必须配置 API Key",
                )
                return

        def mutator(setting):
            setting.third_party_services = services

        configUtil.update_setting(mutator)
        self.return_json({"status": "ok"})


class DeepSeekSearchTestHandler(BaseHandler):
    """POST /config/third_party_services/deepseek/test.json"""

    async def post(self) -> None:
        try:
            req = self.parse_request(DeepSeekSearchTestRequest)
        except ValidationError as e:
            self.return_with_error(error_code="validation_error", error_desc=str(e))
            return

        result = await deepseekService.test_search(req.api_key, req.query)

        if not result.success:
            logger.warning("DeepSeek search service test failed: %s", result.error_message or "")

        self.return_json(result)


class XiaomiMiMoSearchTestHandler(BaseHandler):
    """POST /config/third_party_services/xiaomi_mimo/test.json"""

    async def post(self) -> None:
        try:
            req = self.parse_request(XiaomiMiMoSearchTestRequest)
        except ValidationError as e:
            self.return_with_error(error_code="validation_error", error_desc=str(e))
            return

        result = await xiaomiMimoService.test_search(req.api_key, req.query)

        if not result.success:
            logger.warning("Xiaomi MiMo search service test failed: %s", result.error_message or "")

        self.return_json(result)
