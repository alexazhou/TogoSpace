import json
import os
import sys
import builtins

import pytest
from constants import LlmProviderType
from util import configUtil
from util.configUtil.configTypes import (
    AppConfig,
    LlmProviderConfig,
    SettingConfig,
)

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


def test_runtime_configs_load_from_config_dir(tmp_path):
    os.environ.pop("TEAMAGENT_DB_PATH", None)
    (tmp_path / "setting.json").write_text(json.dumps({
            "version": "v2",
        "default_models": {"primary": "mock-model@mock"},
        "llm_providers": [
            {
                "name": "mock",
                "urls": {"openai": "http://127.0.0.1:9999/v1/chat/completions"},
                "api_key": "test-key",
                "type": "openai",
                "models": [{"name": "mock-model", "protocol": "openai"}]
            }
        ],
        "db_path": "./runtime/test.db",
        "workspace_root": "/tmp/workspaces",
    }), encoding="utf-8")

    app_config = configUtil.load(str(tmp_path))
    llm_cfg = app_config.setting.llm_providers[0]

    assert isinstance(app_config, AppConfig)
    assert llm_cfg.name == "mock"
    assert llm_cfg.urls["openai"] == "http://127.0.0.1:9999/v1/chat/completions"
    assert app_config.setting.db_path == "./runtime/test.db"
    assert app_config.setting.workspace_root == "/tmp/workspaces"



def test_load_returns_appconfig_with_typed_fields(tmp_path):
    (tmp_path / "setting.json").write_text(json.dumps({
            "version": "v2",
        "default_models": {"primary": "gpt-4@svc"},
        "llm_providers": [
            {
                "name": "svc",
                "urls": {"openai": "http://localhost/v1"},
                "api_key": "key-123",
                "type": "openai",
                "models": [{"name": "gpt-4", "protocol": "openai"}]
            }
        ],
        "db_path": "./data/db.sqlite",
    }), encoding="utf-8")

    app_config = configUtil.load(str(tmp_path))
    llm_cfg = app_config.setting.llm_providers[0]

    assert isinstance(app_config, AppConfig)
    assert isinstance(llm_cfg, LlmProviderConfig)
    assert llm_cfg.type == LlmProviderType.OPENAI
    assert isinstance(app_config.setting, SettingConfig)
    assert app_config.setting.db_path == "./data/db.sqlite"
    assert llm_cfg.models[0].name == "gpt-4"
    assert llm_cfg.api_key == "key-123"
    assert isinstance(app_config.role_templates_preset, list)
    assert isinstance(app_config.teams_preset, list)
    assert app_config.setting.workspace_root



def test_load_reads_setting_json_once(tmp_path, monkeypatch):
    setting_file = tmp_path / "setting.json"
    setting_file.write_text(json.dumps({
            "version": "v2",
        "default_models": {"primary": "mock-model@mock"},
        "llm_providers": [
            {
                "name": "mock",
                "urls": {"openai": "http://127.0.0.1:7777/v1/chat/completions"},
                "api_key": "llm-only-key",
                "type": "openai",
                "models": [{"name": "mock-model", "protocol": "openai"}]
            }
        ],
        "db_path": "./runtime/test.db",
        "workspace_root": "/tmp/workspaces",
    }), encoding="utf-8")

    target_path = os.path.abspath(setting_file)
    open_count = {"setting_json": 0}
    real_open = builtins.open

    def _counting_open(path, *args, **kwargs):
        if os.path.abspath(path) == target_path:
            open_count["setting_json"] += 1
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _counting_open)

    configUtil.load(str(tmp_path))

    assert open_count["setting_json"] == 1



def test_load_does_not_create_setting_json_when_missing(tmp_path):
    """`load()` 是纯读：目录在但 `setting.json` 不在 ⇒ **报错，且不补模板**（#380）。

    改造前这条路径会自动从模板补 `setting.json`（并顺手把 README 也写进去），
    于是"只想读配置"的调用方被迫接受写盘。现在读路径必须拒绝，并由报错文案指认路径。
    """
    with pytest.raises(FileNotFoundError) as exc_info:
        configUtil.load(str(tmp_path), force_reload=True)

    assert "配置文件不存在" in str(exc_info.value)
    assert str(tmp_path) in str(exc_info.value), "报错必须指认是哪个路径，不能只说'不存在'"
    assert not (tmp_path / "setting.json").exists(), "读路径不许补模板"
    assert not (tmp_path / "setting.README.md").exists(), "读路径不许写 README"


def test_log_when_config_dir_missing(tmp_path):
    """目录整条不存在 ⇒ 报"配置目录不存在"，且**不创建目录**（#380）。"""
    missing = tmp_path / "nope"
    with pytest.raises(FileNotFoundError) as exc_info:
        configUtil.load(str(missing), force_reload=True)

    assert "配置目录不存在" in str(exc_info.value)
    assert not missing.exists(), "读路径不许建目录"


def test_ensure_config_dir_creates_setting_json_and_readme(tmp_path):
    """初始化入口 `ensure_config_dir()` 负责三个副作用：建目录 + 模板 + README（#380）。"""
    target = tmp_path / "fresh"
    configUtil.ensure_config_dir(str(target))

    setting_file = target / "setting.json"
    readme_file = target / "setting.README.md"

    assert target.is_dir()
    assert setting_file.is_file()
    assert readme_file.is_file(), "初始化入口必须放 README（不再按'是否在测试环境'跳过）"

    setting_data = json.loads(setting_file.read_text(encoding="utf-8"))
    assert setting_data["version"] == "v3"
    assert setting_data["development_mode"] is False
    assert setting_data["llm_providers"][0]["name"] == "deepseek"
    assert setting_data["llm_providers"][0]["models"][0]["name"] == "deepseek-flash"
    # deepseek-flash 是视觉模型：必须显式声明 input，否则缺省 ["text"] 会被当成纯文本
    assert setting_data["llm_providers"][0]["models"][0]["input"] == ["text", "image"]
    assert setting_data["default_models"]["primary"] == "deepseek-flash@deepseek"
    # vision 槽位必须指向唯一支持读图的模型；置空会让 auto_vision_fallback 整条不执行
    assert setting_data["default_models"]["vision"] == "deepseek-flash@deepseek"
    # 模板里两个示例模型的上下文窗口都是 512K（2 的幂口径 524288，不是 512000）
    assert (
        setting_data["llm_providers"][0]["models"][0]["context_config"]["context_window_tokens"]
        == 524288
    )
    assert setting_data["llm_providers"][1]["models"][0]["name"] == "glm-5.3"
    # glm-5.3 是纯文本：serializer 只删 input == ["text"] 的键，所以「本来没写」与「bug 丢了」
    # 只能靠「键不存在」+「非 ["text"] 也不存在」两条一起区分
    assert "input" not in setting_data["llm_providers"][1]["models"][0]
    assert (
        setting_data["llm_providers"][1]["models"][0]["context_config"]["context_window_tokens"]
        == 524288
    )
    # 顶层全局兜底不动（Operator 只要求改模型示例）
    assert setting_data["context_config"]["context_window_tokens"] == 262144



def test_load_setting_ignores_extra_keys(tmp_path):
    (tmp_path / "setting.json").write_text(json.dumps({
            "version": "v2",
        "default_llm_server": "mock",
        "llm_services": [
            {
                "name": "mock",
                "enable": True,
                "base_url": "http://localhost/v1",
                "api_key": "test-key",
                "type": "openai-compatible",
            }
        ],
        "workspace_root": "/tmp/ws",
        "unknown_key": {"keep": False},
    }), encoding="utf-8")

    setting = configUtil.load(str(tmp_path)).setting

    assert setting.default_models.primary.split('@')[1] if setting.default_models.primary else '' == "mock"
    assert setting.workspace_root == "/tmp/ws"



def test_get_app_config_raises_when_cache_is_empty(monkeypatch):
    monkeypatch.setattr(configUtil.core, "_cached_app_config", None)
    monkeypatch.setattr(configUtil.core, "_cached_config_dir", None)
    monkeypatch.setattr(configUtil.core, "_cached_preset_dir", None)

    with pytest.raises(RuntimeError) as exc_info:
        configUtil.get_app_config()

    assert "请先调用 configUtil.load" in str(exc_info.value)



def test_load_json_objects_from_dir_returns_sorted_objects(tmp_path):
    (tmp_path / "b.json").write_text(json.dumps({
            "version": "v2","name": "b"}), encoding="utf-8")
    (tmp_path / "a.json").write_text(json.dumps({
            "version": "v2","name": "a"}), encoding="utf-8")

    items = configUtil.load_json_objects_from_dir(str(tmp_path))

    assert [item["name"] for item in items] == ["a", "b"]



def test_load_json_objects_from_dir_raises_for_non_object(tmp_path):
    (tmp_path / "invalid.json").write_text(json.dumps(["not", "object"]), encoding="utf-8")

    with pytest.raises(ValueError):
        configUtil.load_json_objects_from_dir(str(tmp_path))



