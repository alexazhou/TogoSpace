import os
import sys

import aiohttp

from ...base import ServiceTestCase

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


class _ApiServiceCase(ServiceTestCase):
    """`POST /config/llm.json` 会真实写盘；配置目录已复制到 per-worker 临时目录，
    因此不需要「setup 备份原文 → teardown 写回」这套补丁。"""
    use_custom_config = True


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
