import json
import os
from enum import Enum

import appPaths


class LlmServiceType(str, Enum):
    """V1 配置中的 LLM 服务类型（兼容保留）。"""
    OPENAI_COMPATIBLE = "openai-compatible"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    DEEPSEEK = "deepseek"


def _load_provider_presets() -> dict:
    """加载 provider 预设 URL 列表。"""
    preset_path = os.path.join(appPaths.ASSETS_DIR, "preset", "providerDefaultUrls.json")
    if not os.path.isfile(preset_path):
        return {}
    with open(preset_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _match_provider_by_url(base_url: str, presets: dict) -> tuple[str, str] | None:
    """根据 base_url 匹配预设 provider，返回 (type, label) 或 None。"""
    if not base_url:
        return None
    for provider_type, preset in presets.items():
        if provider_type == "other" or "label" not in preset:
            continue
        for protocol, preset_url in preset.items():
            if protocol == "label":
                continue
            if not isinstance(preset_url, str):
                continue
            if base_url.startswith(preset_url):
                return (provider_type, preset["label"])
    return None


def migrate_v1_to_v2(cfg: dict) -> None:
    """向后兼容自动迁移 (V1 -> V2)"""
    version = cfg.get("version", "v1")
    if version == "v1" or "llm_services" in cfg:
        old_services = cfg.get("llm_services", [])
        presets = _load_provider_presets()
        new_providers = []
        for svc in old_services:
            provider_type = svc.get("type", "openai")
            if provider_type == "openai-compatible":
                provider_type = "openai"

            base_url = svc.get("base_url", "")
            urls = {}
            if base_url:
                urls[provider_type] = base_url

            # 根据 base_url 匹配预设 provider type
            matched = _match_provider_by_url(base_url, presets)
            resolved_type = matched[0] if matched else "other"

            # 处理 V1 中的 reserve_output_tokens 升级 (8192 -> 16384)
            reserve_tokens = svc.get("reserve_output_tokens", 16384)
            if reserve_tokens == 8192:
                reserve_tokens = 16384

            model_config = {
                "name": svc.get("model", "default-model"),
                "protocol": provider_type if provider_type in ("openai", "anthropic") else "openai",
                "enabled": svc.get("enable", True),
                "temperature": svc.get("temperature"),
                "provider_params": svc.get("provider_params", {}),
                "extra_headers": svc.get("extra_headers", {}),
                "context_config": {
                    "context_window_tokens": svc.get("context_window_tokens", 131072),
                    "reserve_output_tokens": reserve_tokens,
                    "compact_trigger_ratio": svc.get("compact_trigger_ratio", 0.85),
                    "compact_summary_max_tokens": svc.get("compact_summary_max_tokens", 6144)
                }
            }

            provider_config = {
                "name": svc.get("name", "migrated-provider"),
                "type": resolved_type,
                "api_key": svc.get("api_key", ""),
                "enable": svc.get("enable", True),
                "urls": urls,
                "models": [model_config]
            }
            new_providers.append(provider_config)

        cfg["llm_providers"] = new_providers

        default_server = cfg.get("default_llm_server")
        if default_server:
            primary_model = ""
            for p in new_providers:
                if p["name"] == default_server and p["models"]:
                    primary_model = f"{p['models'][0]['name']}@{default_server}"
                    break
            if primary_model:
                cfg["default_models"] = {
                    "primary": primary_model,
                    "lite": "",
                    "vision": ""
                }

        cfg.pop("llm_services", None)
        cfg.pop("default_llm_server", None)
        cfg["version"] = "v2"
