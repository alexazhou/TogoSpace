"""tests/unit/service 共用的测试夹具。"""
import pytest

from util.configUtil import core as config_core


@pytest.fixture(autouse=True)
def restore_config_cache():
    """还原 configUtil 的进程级配置缓存，避免测试间串扰。

    configUtil.load() 会写入模块级缓存，而配置加载类单元测试用的是临时目录；
    不还原的话，临时目录被清理后，后续测试仍会拿到指向已删除目录的陈旧配置。
    """
    saved = (
        config_core._cached_app_config,
        config_core._cached_config_dir,
        config_core._cached_preset_dir,
    )
    yield
    (
        config_core._cached_app_config,
        config_core._cached_config_dir,
        config_core._cached_preset_dir,
    ) = saved
