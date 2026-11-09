"""验证测试配置目录写隔离（per-worker 临时目录副本）。

背景：会写配置的接口（`POST /config/llm.json`、`/config/quick_init.json`、
`/system/update_config.json` 等）走 `configUtil.update_setting()` →
`_save_setting_to_file()` 的真实写盘链路，没有「只改内存」的模式。改造前测试直接写在
受版本控制的 `config/` 目录里，靠 `teardown_class` 备份写回来擦掉；teardown 不执行
（Ctrl-C / kill / 用例失败提前退出）就会永久留下 `M` 脏文件。

本文件守住四件事：
1. **入口可达性**：改造后测试仍然走真实写盘链路 —— 临时目录里的 `setting.json`
   内容确实被改写了，而不是因为路径不可写而静默跳过；
2. **隔离性**：同一次写入不会碰到仓库里的源 `config/` 目录；
3. **临时目录被清理**：类级清理后目录真的不存在了（不是"应该被删了"）；
4. **每次都是新副本**：任何一次中断都不影响下一次运行的起始态。
"""
import contextlib
import hashlib
import json
import os
import shutil

import pytest

from tests.api.test_init_controller import test as init_controller_test
from tests.base import ServiceTestCase, _make_isolated_config_dir
from util import configUtil

_REPO_TESTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SHARED_CONFIG_DIR = os.path.join(_REPO_TESTS_DIR, "config")


def _sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class _FakeSharedConfigCase(ServiceTestCase):
    """占位类：只为触发 `_load_config` 的默认（`tests/config`）分支。

    不能用本文件里的 fake 类去测 `use_custom_config = True` 分支：那条分支按
    `sys.modules[cls.__module__].__file__` 定位同级的 `config/`，本文件会解析到
    不存在的 `tests/unit/config`，于是 `_load_config` 在 `isdir` 处提前 return。
    custom 分支统一用仓里真实的 `init_controller_test.TestQuickInit`（它的 `config/` 存在且是空 provider 起始态）。
    """

    use_custom_config = False


@contextlib.contextmanager
def _isolated_config_of(cls):
    """对真实测试类跑一次 `_load_config`，退出时把 patcher 与临时目录都收干净。"""
    cls._load_config()
    try:
        yield cls._isolated_config_dir
    finally:
        cls._safe_cleanup_external_dependencies()


@pytest.fixture
def _reset_config_cache():
    """每个用例后清空 configUtil 的进程缓存，避免 `_cached_config_dir` 串联到别的用例。"""
    yield
    configUtil.load(_SHARED_CONFIG_DIR, force_reload=True)


def test_isolated_dir_is_outside_repo_and_worker_scoped(monkeypatch):
    """临时目录必须落在系统 tmp 下、且目录名带 worker 标识（避免 xdist 并行撞目录）。"""
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw3")

    isolated = _make_isolated_config_dir(_SHARED_CONFIG_DIR, "tests.fake.Case")

    try:
        assert os.path.isdir(isolated)
        # 不在仓库工作树内 —— 这是"不脏 git status"的物理前提
        assert not os.path.abspath(isolated).startswith(os.path.abspath(_REPO_TESTS_DIR))
        # worker 标识体现在父目录上
        assert "gw3" in os.path.basename(os.path.dirname(isolated))
    finally:
        shutil.rmtree(isolated, ignore_errors=True)


def test_load_config_copies_preset_and_teams(_reset_config_cache):
    """`_load_config` 必须连同 `role_templates/`、`teams/` 一起复制。

    为什么必须守这条：`_load_config` 会把**同一个** config 目录同时当作 preset 目录
    传给 `configUtil.load(preset_dir=...)`。只复制 `setting.json` 会让
    `_load_teams()` / `_load_role_templates()` 读到空目录 —— 配置能加载、但团队与
    角色模板静默变空，随后一大堆用例以"找不到 team"的形式失败。
    """
    _FakeSharedConfigCase._load_config()
    isolated = _FakeSharedConfigCase._isolated_config_dir
    try:
        assert isolated is not None and os.path.isdir(isolated)
        for sub in ("role_templates", "teams"):
            assert os.path.isdir(os.path.join(isolated, sub)), f"缺少 {sub}/ 副本"
            assert os.listdir(os.path.join(isolated, sub)), f"{sub}/ 副本为空"
        assert os.path.isfile(os.path.join(isolated, "setting.json"))
    finally:
        _FakeSharedConfigCase._safe_cleanup_isolated_config_dir()


def test_load_config_uses_custom_dir_when_requested(_reset_config_cache):
    """`use_custom_config = True` 时复制的是测试类自己的 `config/`，不是 `tests/config`。

    判据用「内容差异」而不是「路径字符串」：`tests/config/setting.json` 里有 mock
    provider，`test_init_controller/config/setting.json` 是空 provider 起始态。
    只看路径相等会漏掉「路径对了但复制源写错」的变异。
    """
    with _isolated_config_of(init_controller_test.TestQuickInit) as isolated:
        assert init_controller_test.TestQuickInit._source_config_dir.endswith(
            os.path.join("test_init_controller", "config")
        )
        with open(os.path.join(isolated, "setting.json"), encoding="utf-8") as f:
            assert json.load(f)["llm_providers"] == []
        # 反向确认：共享配置是有 provider 的，两者确实不同源
        with open(os.path.join(_SHARED_CONFIG_DIR, "setting.json"), encoding="utf-8") as f:
            assert json.load(f)["llm_providers"] != []


def test_isolated_dir_includes_every_file_of_source(_reset_config_cache):
    """副本必须覆盖源目录的全部文件，不能漏（`setting.README.md` 等也是 tracked）。"""
    _FakeSharedConfigCase._load_config()
    isolated = _FakeSharedConfigCase._isolated_config_dir
    try:
        source_entries = set(os.listdir(_SHARED_CONFIG_DIR))
        isolated_entries = set(os.listdir(isolated))
        assert source_entries <= isolated_entries, (
            f"副本缺少这些条目: {source_entries - isolated_entries}"
        )
    finally:
        _FakeSharedConfigCase._safe_cleanup_isolated_config_dir()


def test_write_lands_in_isolated_dir_not_repo(_reset_config_cache):
    """**核心判据**：真实写盘链路写到临时目录，仓库里的源文件一个字节都没变。

    这条同时钉住两件事，缺一条都会让改造变成"假绿"：
    - 写盘真的发生了（临时目录内容变了）—— 否则改造可能把路径改成不可写、
      接口静默 500，而 `git status` 干净，看起来"隔离成功"；
    - 写盘没有落到仓库（源文件 sha256 不变）。
    """
    source_setting = os.path.join(_SHARED_CONFIG_DIR, "setting.json")
    source_sha_before = _sha256(source_setting)

    _FakeSharedConfigCase._load_config()
    isolated = _FakeSharedConfigCase._isolated_config_dir
    isolated_setting = os.path.join(isolated, "setting.json")
    isolated_sha_before = _sha256(isolated_setting)

    try:
        # 走真实落盘链路（与控制器同一条：update_setting → _save_setting_to_file）
        configUtil.load(isolated, preset_dir=isolated, force_reload=True)
        configUtil.update_setting(lambda s: None)

        isolated_sha_after = _sha256(isolated_setting)
        assert isolated_sha_after != isolated_sha_before, (
            "临时目录里的 setting.json 没有被改写 —— 写盘链路没走到，"
            "「隔离成功」只是假绿"
        )
        with open(isolated_setting, encoding="utf-8") as f:
            written = json.load(f)
        # 保存函数无条件写 version（v2 -> v3），用它证明写的是当前内存态
        assert written["version"] == "v3"

        assert _sha256(source_setting) == source_sha_before, (
            "仓库里的 tests/config/setting.json 被改写了 —— 隔离失效"
        )
    finally:
        _FakeSharedConfigCase._safe_cleanup_isolated_config_dir()


def test_teardown_removes_isolated_dir(_reset_config_cache):
    """类级清理后临时目录必须真的不存在（不给"应该被删了"这种说法）。"""
    _FakeSharedConfigCase._load_config()
    isolated = _FakeSharedConfigCase._isolated_config_dir
    assert os.path.isdir(isolated)

    _FakeSharedConfigCase._safe_cleanup_isolated_config_dir()

    assert not os.path.exists(isolated), f"临时目录未被清理: {isolated}"
    assert _FakeSharedConfigCase._isolated_config_dir is None


def test_each_load_gets_a_fresh_copy(_reset_config_cache):
    """同一个类连续两次 `_load_config` 必须拿到两份独立副本，互不继承写入。

    这是"第二次运行起始状态就变了"那个老问题的直接防线：改造前靠 teardown 写回
    保证起始态，改造后靠"每次 setup 新拷一份"保证，任何一次中断都不影响下一次。
    """
    with _isolated_config_of(init_controller_test.TestQuickInit) as first:
        configUtil.load(first, preset_dir=first, force_reload=True)
        configUtil.update_setting(lambda s: setattr(s, "language", "en-US"))

    with _isolated_config_of(init_controller_test.TestQuickInit) as second:
        assert first != second
        with open(os.path.join(second, "setting.json"), encoding="utf-8") as f:
            fresh = json.load(f)
        # 第二份是源目录的原样副本，看不到第一次写入的 en-US
        assert fresh.get("language") != "en-US"


def test_backend_subprocess_also_points_at_isolated_dir(_reset_config_cache):
    """**两处必须同时指向临时目录**：pytest 进程内的 load 与后端子进程的 --config-dir。

    只改 `configUtil.load` 不改后端子进程，会变成一个很难看的分裂状态：
    pytest 进程读临时副本、后端仍读写仓库里的 tracked config —— 也就是
    「测试文件里的 in-process 断言都过，接口写盘照样脏仓库」。

    这里不真的起后端子进程（起进程成本高、且需要可用端口），而是直接读
    `_start_backend` 构造 env / argv 所依赖的类属性，确保两者同源同一个值。
    """
    import inspect

    from tests import base as base_module

    source = inspect.getsource(base_module.ServiceTestCase._start_backend)
    # 两处**具体绑定行**都必须指向 _backend_config_dir，不能有一处偷用回 _source_config_dir
    assert 'env["TEAMAGENT_PRESET_DIR"] = cls._backend_config_dir' in source, (
        "后端子进程的 TEAMAGENT_PRESET_DIR 没指向临时副本"
    )
    assert 'cmd += ["--config-dir", cls._backend_config_dir]' in source, (
        "后端子进程的 --config-dir 没指向临时副本"
    )
    assert "_source_config_dir" not in source

    with _isolated_config_of(init_controller_test.TestQuickInit) as isolated:
        assert init_controller_test.TestQuickInit._backend_config_dir == isolated
        assert os.path.abspath(isolated) != os.path.abspath(
            init_controller_test.TestQuickInit._source_config_dir
        )
        # 副本内容 == 源目录内容（源目录本身只有 setting.json，没有 teams/ 也要如实一致）
        assert sorted(os.listdir(isolated)) == sorted(
            os.listdir(init_controller_test.TestQuickInit._source_config_dir)
        )
