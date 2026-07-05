import json
import logging
import os
import time

from pydantic import BaseModel, ValidationError

from constants import LlmProtocol
from controller.baseController import BaseHandler
from service import schedulerService
from util import jsonUtil
from util import assertUtil, configUtil, llmApiUtil
from util.configTypes import LlmProviderConfig, LlmModelConfig, LlmContextConfig, DefaultModelSlots
from service.llmService.core import get_provider_url
import appPaths

logger = logging.getLogger(__name__)

SLOT_LABELS = {"primary": "主模型", "lite": "轻量模型", "advanced": "高级模型", "vision": "视觉模型"}


def validate_model_slot_refs(
    providers: list[LlmProviderConfig],
    default_models: DefaultModelSlots,
) -> dict | None:
    """校验 default_models 各槽位引用的模型是否存在于 providers 中。

    返回 None 表示校验通过，否则返回 return_with_error 的参数字典。
    """
    valid_refs: set[str] = set()
    for p in providers:
        for m in p.models:
            valid_refs.add(f"{m.name}@{p.name}")

    for slot_key, slot_label in SLOT_LABELS.items():
        ref = getattr(default_models, slot_key, "")
        if ref and ref not in valid_refs:
            return {
                "error_code": "invalid_model_ref",
                "error_desc": f"{slot_label}引用的模型「{ref}」不存在，请检查服务商和模型配置",
            }
    return None


class LlmConfigHandler(BaseHandler):
    """GET/POST /config/llm.json"""

    async def get(self) -> None:
        setting = configUtil.get_app_config().setting
        
        # Hide sensitive keys if demo mode
        providers = []
        for p in setting.llm_providers:
            item = p.model_dump(mode="json")
            item["has_api_key"] = bool(p.api_key)
            if setting.demo_mode.hide_sensitive:
                item["api_key"] = ""
                item["extra_headers"] = {}
            providers.append(item)
            
        self.return_json({
            "llm_providers": providers,
            "default_models": setting.default_models.model_dump(mode="json"),
            "context_config": setting.context_config.model_dump(exclude_defaults=True, mode="json"),
        })
        
    async def post(self) -> None:
        body = json.loads(self.request.body)
        
        providers_data = body.get("llm_providers", [])
        default_models_data = body.get("default_models", {})
        context_config_data = body.get("context_config", {})
        
        try:
            # 过滤 provider/model 级别 context_config 中的 null 值
            providers_data = [jsonUtil.clean_null_values(p) for p in providers_data]
            providers = [LlmProviderConfig(**p) for p in providers_data]
            default_models = DefaultModelSlots(**default_models_data)
            # 过滤 null 值，让 LlmContextConfig 使用默认值
            context_config_filtered = {k: v for k, v in context_config_data.items() if v is not None}
            context_config = LlmContextConfig(**context_config_filtered)
        except ValidationError as e:
            self.return_with_error(
                error_code="validation_error",
                error_desc=str(e),
            )
            return

        # 校验 default_models 槽位引用的模型是否存在
        error = validate_model_slot_refs(providers, default_models)
        if error:
            self.return_with_error(**error)
            return

        def mutator(s):
            s.llm_providers = providers
            s.default_models = default_models
            s.context_config = context_config

        configUtil.update_setting(mutator)

        if not configUtil.is_initialized():
            schedulerService.stop_schedule("无可用的大模型服务")

        self.return_json({"status": "ok"})


class ProviderTypesHandler(BaseHandler):
    """GET /config/provider_types.json — 返回 providerDefaultUrls.json 原始内容。"""

    async def get(self) -> None:
        preset_path = os.path.join(appPaths.ASSETS_DIR, "preset", "providerDefaultUrls.json")
        if not os.path.isfile(preset_path):
            self.return_json({})
            return
        with open(preset_path, "r", encoding="utf-8") as f:
            presets = json.load(f)
        self.return_json(presets)


class LlmTestRequest(BaseModel):
    provider: dict
    model: dict
    protocol: str | None = None  # 接受字符串，内部转换为 LlmProtocol


class LlmTestHandler(BaseHandler):
    """POST /config/llm_test.json"""

    async def post(self) -> None:
        try:
            req = self.parse_request(LlmTestRequest)
            provider_config = LlmProviderConfig(**jsonUtil.clean_null_values(req.provider))
            model_config = LlmModelConfig(**jsonUtil.clean_null_values(req.model))
        except ValidationError as e:
            self.return_with_error(error_code="validation_error", error_desc=str(e))
            return

        protocol = req.protocol or model_config.protocol.value
            
        try:
            result = await _test_llm_service(provider_config, model_config, protocol)
            self.return_json({
                "status": "ok",
                "message": "连接成功",
                "detail": result,
            })
        except Exception as e:
            logger.warning(f"LLM 可用性测试失败: {e}")
            self.return_json({
                "status": "error",
                "message": str(e),
                "detail": {
                    "error_type": type(e).__name__,
                    "raw_error": str(e),
                },
            })


async def _test_llm_service(provider: LlmProviderConfig, model: LlmModelConfig, protocol: str) -> dict:
    url = get_provider_url(provider, protocol)
    request = llmApiUtil.build_agent_probe_request(
        model=model.name,
        provider_params={**provider.provider_params, **model.provider_params},
    )

    start_time = time.monotonic()
    response = await llmApiUtil.send_request_stream(
        request,
        url,
        provider.api_key,
        custom_llm_provider=protocol,
        extra_headers=provider.extra_headers,
    )
    duration_ms = int((time.monotonic() - start_time) * 1000)

    message = response.choices[0].message if response.choices else None
    response_text = ""
    if message:
        response_text = message.content or ""
        # 有些模型把内容放在 reasoning_content 里
        if not response_text and message.reasoning_content:
            response_text = message.reasoning_content
        # 如果 content 为空但有 tool_calls，显示 tool_calls 信息
        if not response_text and message.tool_calls:
            tool_names = [tc.function_name for tc in message.tool_calls]
            response_text = f"[tool_calls] {', '.join(tool_names)}"

    return {
        "model": model.name,
        "response_text": response_text,
        "duration_ms": duration_ms,
        "usage": response.usage.model_dump() if response.usage else None,
        "test_mode": "agent_probe_stream_with_tools",
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


class SkillListHandler(BaseHandler):
    """GET /config/skills/list.json — 返回系统可用的 Skill 列表。"""

    async def get(self) -> None:
        import service.skillService as skillService
        skills = skillService.get_all_skills()
        self.return_json({
            "skills": [
                {
                    "name": s.name,
                    "description": s.description,
                    "is_builtin": s.is_builtin,
                    "files": s.files
                }
                for s in skills
            ],
        })


class ToolListHandler(BaseHandler):
    """GET /config/tools/list.json — 返回系统可用的 Tool 列表。"""

    async def get(self) -> None:
        from service.agentService.toolRegistry import CATEGORY_CONFIG
        tools = []
        for name, category in CATEGORY_CONFIG.items():
            if category.name not in ("ADMIN", "BASIC"):
                tools.append({"name": name, "category": category.name})
        
        # Add predefined categories
        tools.extend([
            {"name": "Category:Read", "category": "CATEGORY"},
            {"name": "Category:Write", "category": "CATEGORY"},
            {"name": "Category:Execute", "category": "CATEGORY"},
        ])
        
        self.return_json({"tools": tools})
