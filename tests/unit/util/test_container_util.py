"""`util.containerUtil` 的 argv 形状测试（§3.2 形态 A / B）。

这里是纯字符串构造，直接断言 argv：两种形态不能互换——对自建容器用 `exec`
会因容器未 running 而失败；对外部容器用 `start` 会二次启动主进程。
"""

from __future__ import annotations

from constants import CONTAINER_GTSP_PATH
from util import containerUtil


def test_container_start_and_exec_argv_shapes():
    """形态 A `start -a -i` / 形态 B `exec -i`（§3.2 两种启动方式，不能互换）。"""
    assert containerUtil.build_container_start_argv("c0ffee123456") == [
        "start", "-a", "-i", "c0ffee123456",
    ]
    exec_argv = containerUtil.build_container_exec_argv("c0ffee123456")
    assert exec_argv[:4] == ["exec", "-i", "c0ffee123456", CONTAINER_GTSP_PATH]
    assert exec_argv[4:6] == ["--mode", "stdio"]
    # 形态 B 绝不能带 -a / start：外部容器主进程不能被动
    assert "-a" not in exec_argv
    assert "start" not in exec_argv


def test_container_attach_argv_dispatches_by_container_type():
    """按 `is_managed` 选择形态 A / B；调用方不再自己分支（§3.2）。"""
    assert containerUtil.build_container_attach_argv(
        "c0ffee123456", is_managed=True
    ) == containerUtil.build_container_start_argv("c0ffee123456")
    assert containerUtil.build_container_attach_argv(
        "deadbeef0002", is_managed=False
    ) == containerUtil.build_container_exec_argv("deadbeef0002")
