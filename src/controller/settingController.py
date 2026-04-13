import json
import logging
import time

from pydantic import BaseModel, ValidationError

from constants import LlmServiceType, OpenaiApiRole
from controller.baseController import BaseHandler
from service import schedulerService
from util import assertUtil, configUtil
from util.configTypes import LlmServiceConfig
from util.llmApiUtil.OpenAiModels import OpenAIRequest, OpenAIMessage

logger = logging.getLogger(__name__)

# LiteLLM custom_llm_provider 映射（与 llmService 保持一致）
_TYPE_TO_PROVIDER = {
    LlmServiceType.OPENAI_COMPATIBLE: "openai",
    LlmServiceType.ANTHROPIC: "anthropic",
    LlmServiceType.GOOGLE: "gemini",
    LlmServiceType.DEEPSEEK: "deepseek",
}


class TestLlmServiceRequest(BaseModel):
    """连通性测试请求，通过 mode 字段区分已保存服务和临时配置。"""
    mode: str  # "saved" | "temp"
    index: int | None = None
    base_url: str | None = None
    api_key: str | None = None
    type: str | None = None
    model: str | None = None
    extra_headers: dict[str, str] | None = None


def _get_setting():
    return configUtil.get_app_config().setting


def _validate_index(index_str: str) -> int:
    """将路径参数转为合法的数组下标。"""
    index = int(index_str)
    services = _get_setting().llm_services
    assertUtil.assertTrue(
        0 <= index < len(services),
        error_message=f"服务序号 {index} 越界，当前共 {len(services)} 个服务",
        error_code="index_out_of_range",
    )
    return index


class LlmServiceListHandler(BaseHandler):
    """GET /config/llm_services/list.json"""

    async def get(self) -> None:
        setting = _get_setting()
        self.return_json({
            "llm_services": [
                s.model_dump(exclude_unset=True, mode="json") for s in setting.llm_services
            ],
            "default_llm_server": setting.default_llm_server,
        })


class LlmServiceCreateHandler(BaseHandler):
    """POST /config/llm_services/create.json"""

    async def post(self) -> None:
        try:
            new_service = self.parse_request(LlmServiceConfig)
        except ValidationError as e:
            self.return_with_error(
                error_code="validation_error",
                error_desc=str(e),
            )
            return

        setting = _get_setting()

        # 校验名称不重复
        existing_names = {s.name for s in setting.llm_services}
        assertUtil.assertTrue(
            new_service.name not in existing_names,
            error_message=f"服务名称 '{new_service.name}' 已存在",
            error_code="name_duplicate",
        )

        # 校验 base_url 格式
        assertUtil.assertTrue(
            new_service.base_url.startswith("http://") or new_service.base_url.startswith("https://"),
            error_message="base_url 必须以 http:// 或 https:// 开头",
            error_code="invalid_base_url",
        )

        def mutator(s):
            s.llm_services.append(new_service)

        configUtil.update_setting(mutator)
        self.return_json({"status": "ok", "index": len(setting.llm_services) - 1})


class LlmServiceModifyHandler(BaseHandler):
    """POST /config/llm_services/{index}/modify.json"""

    async def post(self, index_str: str) -> None:
        index = _validate_index(index_str)
        setting = _get_setting()
        service = setting.llm_services[index]

        body = json.loads(self.request.body)
        known_fields = set(LlmServiceConfig.model_fields.keys()) - {"name"}
        updates = {k: v for k, v in body.items() if k in known_fields}

        assertUtil.assertTrue(
            len(updates) > 0,
            error_message="未提供任何可修改的字段",
            error_code="no_update_fields",
        )

        # 不能禁用当前默认服务
        if "enable" in updates and updates["enable"] is False:
            assertUtil.assertTrue(
                service.name != setting.default_llm_server,
                error_message="不能禁用当前默认服务，请先切换默认服务",
                error_code="cannot_disable_default",
            )

        # 校验 base_url 格式
        if "base_url" in updates:
            url = updates["base_url"]
            assertUtil.assertTrue(
                isinstance(url, str) and (url.startswith("http://") or url.startswith("https://")),
                error_message="base_url 必须以 http:// 或 https:// 开头",
                error_code="invalid_base_url",
            )

        # dict 合并重建，Pydantic 自动校验
        current = service.model_dump(exclude_unset=True)
        current.update(updates)
        try:
            new_service = LlmServiceConfig(**current)
        except ValidationError as e:
            self.return_with_error(
                error_code="validation_error",
                error_desc=str(e),
            )
            return

        def mutator(s):
            s.llm_services[index] = new_service

        configUtil.update_setting(mutator)

        if not configUtil.is_initialized():
            schedulerService.stop_schedule("无可用的大模型服务")

        self.return_json({"status": "ok"})


class LlmServiceDeleteHandler(BaseHandler):
    """POST /config/llm_services/{index}/delete.json"""

    async def post(self, index_str: str) -> None:
        index = _validate_index(index_str)
        setting = _get_setting()
        service = setting.llm_services[index]

        assertUtil.assertTrue(
            service.name != setting.default_llm_server,
            error_message="不能删除当前默认服务，请先切换默认服务",
            error_code="cannot_delete_default",
        )

        def mutator(s):
            s.llm_services.pop(index)

        configUtil.update_setting(mutator)

        # 删除服务后检查：若无可用 LLM 服务，阻塞调度
        if not configUtil.is_initialized():
            schedulerService.stop_schedule("无可用的大模型服务")

        self.return_json({"status": "ok", "deleted_name": service.name})


class LlmServiceSetDefaultHandler(BaseHandler):
    """POST /config/llm_services/{index}/set_default.json"""

    async def post(self, index_str: str) -> None:
        index = _validate_index(index_str)
        setting = _get_setting()
        service = setting.llm_services[index]

        assertUtil.assertTrue(
            service.enable,
            error_message=f"服务 '{service.name}' 已禁用，无法设为默认",
            error_code="cannot_default_disabled",
        )

        def mutator(s):
            s.default_llm_server = service.name

        configUtil.update_setting(mutator)
        self.return_json({"status": "ok", "default_llm_server": service.name})


class LlmServiceTestHandler(BaseHandler):
    """POST /config/llm_services/test.json"""

    async def post(self) -> None:
        request = self.parse_request(TestLlmServiceRequest)

        assertUtil.assertTrue(
            request.mode in ("saved", "temp"),
            error_message="mode 必须为 'saved' 或 'temp'",
            error_code="invalid_mode",
        )

        if request.mode == "saved":
            assertUtil.assertNotNull(
                request.index,
                error_message="mode='saved' 时必须提供 index",
                error_code="missing_index",
            )
            setting = _get_setting()
            assertUtil.assertTrue(
                0 <= request.index < len(setting.llm_services),
                error_message=f"服务序号 {request.index} 越界",
                error_code="index_out_of_range",
            )
            config = setting.llm_services[request.index]
        else:
            assertUtil.assertNotNull(request.base_url, error_message="mode='temp' 时必须提供 base_url", error_code="missing_field")
            assertUtil.assertNotNull(request.api_key, error_message="mode='temp' 时必须提供 api_key", error_code="missing_field")
            assertUtil.assertNotNull(request.type, error_message="mode='temp' 时必须提供 type", error_code="missing_field")
            assertUtil.assertNotNull(request.model, error_message="mode='temp' 时必须提供 model", error_code="missing_field")

            config = LlmServiceConfig(
                name="__test__",
                base_url=request.base_url,
                api_key=request.api_key,
                type=LlmServiceType(request.type),
                model=request.model,
                extra_headers=request.extra_headers or {},
            )

        # 执行连通性测试
        try:
            result = await _test_llm_service(config)
            self.return_json({
                "status": "ok",
                "message": "连接成功",
                "detail": result,
            })
        except Exception as e:
            logger.warning(f"LLM 连通性测试失败: {e}")
            self.return_json({
                "status": "error",
                "message": str(e),
                "detail": {
                    "error_type": type(e).__name__,
                    "raw_error": str(e),
                },
            })


async def _test_llm_service(config: LlmServiceConfig) -> dict:
    """向目标 LLM 服务发送一个最小推理请求，验证连通性。"""
    from util import llmApiUtil

    provider = _TYPE_TO_PROVIDER.get(config.type)

    request = OpenAIRequest(
        model=config.model,
        messages=[OpenAIMessage.text(OpenaiApiRole.USER, "hi")],
        max_tokens=16,
        tools=None,
        tool_choice=None,
    )

    start_time = time.monotonic()
    response = await llmApiUtil.send_request_non_stream(
        request,
        config.base_url,
        config.api_key,
        custom_llm_provider=provider,
        extra_headers=config.extra_headers,
    )
    duration_ms = int((time.monotonic() - start_time) * 1000)

    return {
        "model": config.model,
        "response_text": response.choices[0].message.content if response.choices else "",
        "duration_ms": duration_ms,
        "usage": response.usage.model_dump() if response.usage else None,
    }


_SUPPORTED_LANGUAGES = {"zh-CN", "en"}


class LanguageHandler(BaseHandler):
    """POST /config/language.json — 设置界面语言偏好。"""

    async def post(self) -> None:
        body = json.loads(self.request.body)
        lang = body.get("language", "")
        assertUtil.assertTrue(
            lang in _SUPPORTED_LANGUAGES,
            error_message=f"不支持的语言：{lang!r}，可选值：{sorted(_SUPPORTED_LANGUAGES)}",
            error_code="unsupported_language",
        )
        configUtil.set_language(lang)
        self.return_json({"status": "ok", "language": lang})
