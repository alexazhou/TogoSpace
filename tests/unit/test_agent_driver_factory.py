import os
import sys

from service.agentService.driver import normalize_driver_config

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


def test_normalize_driver_config_defaults_to_native():
    cfg = normalize_driver_config({"name": "alice", "model": "test"})
    assert cfg.driver_type == "native"
    assert cfg.options == {}


def test_normalize_driver_config_supports_legacy_claude_sdk_fields():
    cfg = normalize_driver_config(
        {
            "name": "alice",
            "model": "test",
            "use_agent_sdk": True,
            "allowed_tools": ["Read", "Write"],
        }
    )
    assert cfg.driver_type == "claude_sdk"
    assert cfg.options["allowed_tools"] == ["Read", "Write"]


def test_normalize_driver_config_prefers_explicit_driver_block():
    cfg = normalize_driver_config(
        {
            "name": "alice",
            "model": "test",
            "use_agent_sdk": True,
            "allowed_tools": ["Old"],
            "driver": {
                "type": "claude_sdk",
                "allowed_tools": ["Read"],
                "max_turns": 50,
            },
        }
    )
    assert cfg.driver_type == "claude_sdk"
    assert cfg.options == {"allowed_tools": ["Read"], "max_turns": 50}


def test_normalize_driver_config_supports_legacy_runtime_block():
    cfg = normalize_driver_config(
        {
            "name": "alice",
            "model": "test",
            "runtime": {
                "type": "claude_sdk",
                "allowed_tools": ["Read"],
                "max_turns": 80,
            },
        }
    )
    assert cfg.driver_type == "claude_sdk"
    assert cfg.options == {"allowed_tools": ["Read"], "max_turns": 80}
