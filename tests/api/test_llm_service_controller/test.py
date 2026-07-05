import os
import sys

import aiohttp

from ...base import ServiceTestCase

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

_CONFIG_DIR = os.path.join(os.path.dirname(__file__), "config")
_SETTING_PATH = os.path.join(_CONFIG_DIR, "setting.json")


class _ApiServiceCase(ServiceTestCase):
    use_custom_config = True
    _original_setting: str = None

    @classmethod
    def setup_class(cls) -> None:
        with open(_SETTING_PATH, "r", encoding="utf-8") as f:
            cls._original_setting = f.read()
        super().setup_class()

    @classmethod
    def teardown_class(cls) -> None:
        super().teardown_class()
        if cls._original_setting is not None:
            with open(_SETTING_PATH, "w", encoding="utf-8") as f:
                f.write(cls._original_setting)


class TestLlmConfigController(_ApiServiceCase):
    requires_backend = True

    async def test_get_llm_config(self):
        async with aiohttp.ClientSession() as client:
            async with client.get(f"{self.backend_base_url}/config/llm.json") as resp:
                assert resp.status == 200
                data = await resp.json()

        assert "llm_providers" in data
        assert "default_models" in data
        assert "context_config" in data
        assert len(data["llm_providers"]) >= 1

    async def test_post_llm_config(self):
        async with aiohttp.ClientSession() as client:
            # First get the current config
            async with client.get(f"{self.backend_base_url}/config/llm.json") as resp:
                data = await resp.json()

            # Modify a provider's name
            providers = data["llm_providers"]
            old_name = providers[0]["name"]
            providers[0]["name"] = "test-renamed-provider"
            
            # Also need to update default_models since we renamed the provider
            default_models = data["default_models"]
            for slot, val in default_models.items():
                if val and val.endswith(f"@{old_name}"):
                    model = val.split("@")[0]
                    default_models[slot] = f"{model}@test-renamed-provider"

            # Post the updated config
            async with client.post(
                f"{self.backend_base_url}/config/llm.json",
                json={
                    "llm_providers": providers,
                    "default_models": default_models,
                    "context_config": data.get("context_config", {})
                }
            ) as resp:
                assert resp.status == 200
                res_data = await resp.json()
                assert res_data["status"] == "ok"

            # Get again to verify the name was saved
            async with client.get(f"{self.backend_base_url}/config/llm.json") as resp:
                data2 = await resp.json()
                
            assert data2["llm_providers"][0]["name"] == "test-renamed-provider"
