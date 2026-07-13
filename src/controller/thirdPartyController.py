import json
import logging

from pydantic import BaseModel, ValidationError

from controller.baseController import BaseHandler
from service.thirdPartyService import deepseekService
from util import configUtil, jsonUtil
from util.configTypes import DeepSeekThirdPartyServiceConfig, ThirdPartyServicesConfig

logger = logging.getLogger(__name__)


class ThirdPartyServicesPayload(BaseModel):
    third_party_services: ThirdPartyServicesConfig


class DeepSeekSearchTestRequest(BaseModel):
    enabled: bool = True
    api_key: str = ""
    query: str = deepseekService.DEFAULT_SEARCH_QUERY


class ThirdPartyServicesConfigHandler(BaseHandler):
    """GET/POST /config/third_party_services.json"""

    async def get(self) -> None:
        setting = configUtil.get_app_config().setting
        services = setting.third_party_services.model_dump(mode="json")
        deepseek = services.setdefault("deepseek", {})
        deepseek["has_api_key"] = bool(setting.third_party_services.deepseek.api_key)
        if setting.demo_mode.hide_sensitive:
            deepseek["api_key"] = ""

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

        def mutator(setting):
            setting.third_party_services = services

        configUtil.update_setting(mutator)
        self.return_json({"status": "ok"})


class DeepSeekSearchTestHandler(BaseHandler):
    """POST /config/third_party_services/deepseek/test.json"""

    async def post(self) -> None:
        try:
            req = self.parse_request(DeepSeekSearchTestRequest)
            service_config = DeepSeekThirdPartyServiceConfig(
                enabled=req.enabled,
                api_key=req.api_key,
            )
        except ValidationError as e:
            self.return_with_error(error_code="validation_error", error_desc=str(e))
            return

        result = await deepseekService.test_search(service_config.api_key, req.query)
        if not result.get("success"):
            logger.warning("DeepSeek search service test failed: %s", result.get("message", ""))
        self.return_json(result)
