import re

with open("tests/unit/service/test_main_config_loading.py", "r") as f:
    content = f.read()

# Fix imports
content = content.replace("LlmServiceConfig", "LlmProviderConfig")
content = content.replace("LlmServiceType", "LlmProviderType")

# Replace mock configs
content = content.replace(
    '''"default_llm_server": "mock",
        "llm_services": [
            {
                "name": "mock",
                "enable": True,
                "base_url": "http://127.0.0.1:9999/v1/chat/completions",
                "api_key": "test-key",
                "type": "openai-compatible",
            }
        ]''',
    '''"default_models": {"primary": "mock-model@mock"},
        "llm_providers": [
            {
                "name": "mock",
                "urls": {"openai": "http://127.0.0.1:9999/v1/chat/completions"},
                "api_key": "test-key",
                "type": "openai",
                "models": [{"name": "mock-model", "protocol": "openai"}]
            }
        ]'''
)

content = content.replace(
    '''"default_llm_server": "mock_disabled",
        "llm_services": [
            {
                "name": "mock_disabled",
                "enable": False,
                "base_url": "http://127.0.0.1:1111/v1/chat/completions",
                "api_key": "disabled-key",
                "type": "openai-compatible",
            },
            {
                "name": "mock",
                "enable": True,
                "base_url": "http://127.0.0.1:8888/v1/chat/completions",
                "api_key": "app-key",
                "type": "openai-compatible",
            }
        ]''',
    '''"default_models": {"primary": "mock-model@mock"},
        "llm_providers": [
            {
                "name": "mock",
                "urls": {"openai": "http://127.0.0.1:8888/v1/chat/completions"},
                "api_key": "app-key",
                "type": "openai",
                "models": [{"name": "mock-model", "protocol": "openai"}]
            }
        ]'''
)

content = content.replace(
    '''"default_llm_server": "mock",
        "llm_services": [
            {
                "name": "mock",
                "enable": True,
                "base_url": "http://127.0.0.1:7777/v1/chat/completions",
                "api_key": "llm-only-key",
                "type": "openai-compatible",
            }
        ]''',
    '''"default_models": {"primary": "mock-model@mock"},
        "llm_providers": [
            {
                "name": "mock",
                "urls": {"openai": "http://127.0.0.1:7777/v1/chat/completions"},
                "api_key": "llm-only-key",
                "type": "openai",
                "models": [{"name": "mock-model", "protocol": "openai"}]
            }
        ]'''
)

content = content.replace(
    '''"default_llm_server": "svc",
        "llm_services": [
            {
                "name": "svc",
                "enable": True,
                "base_url": "http://localhost/v1",
                "api_key": "key-123",
                "type": "openai-compatible",
                "model": "gpt-4",
            }
        ]''',
    '''"default_models": {"primary": "gpt-4@svc"},
        "llm_providers": [
            {
                "name": "svc",
                "urls": {"openai": "http://localhost/v1"},
                "api_key": "key-123",
                "type": "openai",
                "models": [{"name": "gpt-4", "protocol": "openai"}]
            }
        ]'''
)

content = content.replace(
    '''"default_llm_server": "svc",
        "llm_services": [
            {
                "name": "svc",
                "enable": True,
                "base_url": "http://localhost/v1",
                "api_key": "key-123",
                "type": "openai-compatible",
            }
        ]''',
    '''"default_models": {"primary": "mock-model@svc"},
        "llm_providers": [
            {
                "name": "svc",
                "urls": {"openai": "http://localhost/v1"},
                "api_key": "key-123",
                "type": "openai",
                "models": [{"name": "mock-model", "protocol": "openai"}]
            }
        ]'''
)

content = content.replace(
    '''"default_llm_server": "svc",
        "llm_services": [
            {
                "name": "svc",
                "enable": True,
                "base_url": "http://localhost/v1",
                "api_key": "key-123",
                "type": "openai-compatible",
                "extra_headers": {
                    "X-Client-Name": "openclaw",
                },
            }
        ]''',
    '''"default_models": {"primary": "mock-model@svc"},
        "llm_providers": [
            {
                "name": "svc",
                "urls": {"openai": "http://localhost/v1"},
                "api_key": "key-123",
                "type": "openai",
                "extra_headers": {
                    "X-Client-Name": "openclaw",
                },
                "models": [{"name": "mock-model", "protocol": "openai"}]
            }
        ]'''
)

content = content.replace(
    '''"default_llm_server": "svc",
        "llm_services": [
            {
                "name": "svc",
                "enable": True,
                "base_url": "http://localhost/v1",
                "api_key": "key-123",
                "type": "openai-compatible",
                "provider_params": {
                    "reasoning_effort": "high",
                },
            }
        ]''',
    '''"default_models": {"primary": "mock-model@svc"},
        "llm_providers": [
            {
                "name": "svc",
                "urls": {"openai": "http://localhost/v1"},
                "api_key": "key-123",
                "type": "openai",
                "provider_params": {
                    "reasoning_effort": "high",
                },
                "models": [{"name": "mock-model", "protocol": "openai"}]
            }
        ]'''
)

content = content.replace(
    '''"default_llm_server": "svc",
        "llm_services": [
            {
                "name": "svc",
                "enable": True,
                "base_url": "http://localhost/v1",
                "api_key": "key-123",
                "type": "openai-compatible",
                "provider_params": {
                    "model": "other-model",
                },
            }
        ]''',
    '''"default_models": {"primary": "mock-model@svc"},
        "llm_providers": [
            {
                "name": "svc",
                "urls": {"openai": "http://localhost/v1"},
                "api_key": "key-123",
                "type": "openai",
                "provider_params": {
                    "model": "other-model",
                },
                "models": [{"name": "mock-model", "protocol": "openai"}]
            }
        ]'''
)

content = content.replace(
    '''llm_services=[
            {
                "name": "mock",
                "enable": True,
                "base_url": "http://localhost/v1",
                "api_key": "key-123",
                "type": "openai-compatible",
            }
        ]''',
    '''default_models={"primary": "mock-model@mock"},
        llm_providers=[
            {
                "name": "mock",
                "urls": {"openai": "http://localhost/v1"},
                "api_key": "key-123",
                "type": "openai",
                "models": [{"name": "mock-model", "protocol": "openai"}]
            }
        ]'''
)

content = content.replace(
    '''"llm_services": [],
        "default_llm_server": None,''',
    '''"llm_providers": [],
        "default_models": {"primary": None},'''
)

content = content.replace(
    '''"llm_services": [
            {
                "name": "disabled",
                "enable": False,
                "base_url": "http://localhost/v1",
                "api_key": "key",
                "type": "openai-compatible",
            }
        ],
        "default_llm_server": "disabled",''',
    '''"llm_providers": [],
        "default_models": {"primary": "disabled@disabled"},'''
)

content = content.replace(
    '''"llm_services": [
            {
                "name": "mock",
                "enable": True,
                "base_url": "http://localhost/v1",
                "api_key": "key",
                "type": "openai-compatible",
            }
        ],
        "default_llm_server": "mock",''',
    '''"llm_providers": [
            {
                "name": "mock",
                "urls": {"openai": "http://localhost/v1"},
                "api_key": "key",
                "type": "openai",
                "models": [{"name": "mock-model", "protocol": "openai"}]
            }
        ],
        "default_models": {"primary": "mock-model@mock"},'''
)

content = content.replace('app_config.setting.current_llm_service', 'app_config.setting.llm_providers[0]')

# Fix assert for type enum
content = content.replace('LlmProviderType.OPENAI_COMPATIBLE', 'LlmProviderType.OPENAI')

with open("tests/unit/service/test_main_config_loading.py", "w") as f:
    f.write(content)
