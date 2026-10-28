from types import SimpleNamespace

from constants import DESKTOP_ALLOWED_ORIGINS, DEVELOPMENT_ALLOWED_ORIGINS
from controller import baseController


def _config(development_mode: bool):
    return SimpleNamespace(setting=SimpleNamespace(development_mode=development_mode))


def test_desktop_origins_always_allowed(monkeypatch):
    monkeypatch.setattr(baseController.configUtil, "get_app_config", lambda: _config(False))

    origins = baseController.allowed_web_origins()

    assert set(DESKTOP_ALLOWED_ORIGINS) <= origins
    assert not (set(DEVELOPMENT_ALLOWED_ORIGINS) & origins)


def test_dev_origins_only_in_development_mode(monkeypatch):
    monkeypatch.setattr(baseController.configUtil, "get_app_config", lambda: _config(True))

    origins = baseController.allowed_web_origins()

    assert set(DESKTOP_ALLOWED_ORIGINS) <= origins
    assert set(DEVELOPMENT_ALLOWED_ORIGINS) <= origins


def test_config_not_loaded_falls_back_to_desktop_origins(monkeypatch):
    def _raise():
        raise RuntimeError("config not loaded")

    monkeypatch.setattr(baseController.configUtil, "get_app_config", _raise)

    assert baseController.allowed_web_origins() == set(DESKTOP_ALLOWED_ORIGINS)
