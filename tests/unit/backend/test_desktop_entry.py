import io
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import desktopEntry


class TestReadDesktopParams:
    def test_missing_flag(self):
        with pytest.raises(desktopEntry.DesktopParamError) as exc_info:
            desktopEntry._read_desktop_params({})
        assert exc_info.value.code == desktopEntry.ERROR_INVALID_ENV

    def test_invalid_port(self):
        with pytest.raises(desktopEntry.DesktopParamError):
            desktopEntry._read_desktop_params({
                "TOGOSPACE_DESKTOP": "1",
                "TOGOSPACE_DESKTOP_PORT": "abc",
                "TOGOSPACE_DESKTOP_TOKEN": "token",
            })

    def test_port_out_of_range(self):
        with pytest.raises(desktopEntry.DesktopParamError):
            desktopEntry._read_desktop_params({
                "TOGOSPACE_DESKTOP": "1",
                "TOGOSPACE_DESKTOP_PORT": "70000",
                "TOGOSPACE_DESKTOP_TOKEN": "token",
            })

    def test_empty_token(self):
        with pytest.raises(desktopEntry.DesktopParamError):
            desktopEntry._read_desktop_params({
                "TOGOSPACE_DESKTOP": "1",
                "TOGOSPACE_DESKTOP_PORT": "8180",
                "TOGOSPACE_DESKTOP_TOKEN": "   ",
            })

    def test_valid_params(self):
        params = desktopEntry._read_desktop_params({
            "TOGOSPACE_DESKTOP": "1",
            "TOGOSPACE_DESKTOP_PORT": "18180",
            "TOGOSPACE_DESKTOP_TOKEN": "secret",
            "TOGOSPACE_PARENT_PID": "123",
        })
        assert params.port == 18180
        assert params.token == "secret"
        assert params.parent_pid == 123

    def test_invalid_parent_pid_ignored(self):
        params = desktopEntry._read_desktop_params({
            "TOGOSPACE_DESKTOP": "1",
            "TOGOSPACE_DESKTOP_PORT": "8180",
            "TOGOSPACE_DESKTOP_TOKEN": "secret",
            "TOGOSPACE_PARENT_PID": "not-a-pid",
        })
        assert params.parent_pid is None


class TestMarkers:
    def test_ready_marker_format(self, capsys):
        desktopEntry.emit_ready("127.0.0.1", 8180)
        assert capsys.readouterr().out == "[TOGO] READY url=http://127.0.0.1:8180\n"

    def test_migration_markers(self, capsys):
        desktopEntry.emit_migration_start()
        desktopEntry.emit_migration_end()
        assert capsys.readouterr().out.splitlines() == [
            "[TOGO] MIGRATION_START",
            "[TOGO] MIGRATION_END",
        ]

    def test_error_marker_is_single_line(self, capsys):
        desktopEntry.emit_error(2, "第一行\n第二行")
        out = capsys.readouterr().out
        assert out == "[TOGO] ERROR code=2 message=第一行 第二行\n"


class TestOutputEncoding:
    """#245：Windows 上 stdout 默认 cp1252，中文 marker 会抛 UnicodeEncodeError 吞掉真实错误。"""

    @staticmethod
    def _cp1252_stdout() -> tuple[io.BytesIO, io.TextIOWrapper]:
        raw = io.BytesIO()
        return raw, io.TextIOWrapper(raw, encoding="cp1252", errors="strict", newline="\n")

    def test_configure_output_encoding_makes_chinese_readable(self, monkeypatch):
        raw, stream = self._cp1252_stdout()
        monkeypatch.setattr(desktopEntry.sys, "stdout", stream)
        monkeypatch.setattr(desktopEntry.sys, "stderr", stream)

        desktopEntry._configure_output_encoding()
        desktopEntry.emit_error(desktopEntry.ERROR_INVALID_ENV, "TOGOSPACE_DESKTOP 必须为 1")
        stream.flush()

        text = raw.getvalue().decode("utf-8")  # 真按 UTF-8 写出来了
        assert "[TOGO] ERROR code=1 message=TOGOSPACE_DESKTOP 必须为 1" in text
        assert "\\u" not in text  # 不是转义形态：用户看到的是中文

    def test_emit_marker_falls_back_when_stream_cannot_reconfigure(self, monkeypatch):
        """流没有 reconfigure（pythonw / 特殊重定向）时，marker 仍必须以转义形态送出去。"""

        class ExplodingStream:
            def __init__(self):
                self.buffer = io.BytesIO()

            def write(self, _text):  # 模拟 cp1252 strict 下 print 抛错
                raise UnicodeEncodeError("charmap", "必", 0, 1, "character maps to <undefined>")

            def flush(self):
                pass

        stream = ExplodingStream()
        monkeypatch.setattr(desktopEntry.sys, "stdout", stream)

        desktopEntry._configure_output_encoding()  # 无 reconfigure ⇒ 静默跳过
        desktopEntry.emit_error(desktopEntry.ERROR_INVALID_ENV, "必须为 1")  # 不得抛异常

        written = stream.buffer.getvalue()
        assert b"[TOGO] ERROR code=1 message=" in written
        assert b"\\u5fc5" in written  # 中文退化成转义，但 code 已送达外壳


class TestApplyDesktopOverrides:
    @staticmethod
    def _make_setting(bind_host="0.0.0.0", bind_port=8180, auth_enabled=False, token=""):
        return SimpleNamespace(
            bind_host=bind_host,
            bind_port=bind_port,
            auth=SimpleNamespace(enabled=auth_enabled, token=token),
        )

    def test_forces_local_bind_and_auth(self, monkeypatch):
        setting = self._make_setting()
        app_config = SimpleNamespace(setting=setting)
        monkeypatch.setattr(desktopEntry.configUtil, "get_app_config", lambda: app_config)
        update_setting = MagicMock()
        monkeypatch.setattr(desktopEntry.configUtil, "update_setting", update_setting)

        changed = desktopEntry._apply_desktop_overrides(
            desktopEntry.DesktopParams(port=18180, token="secret", parent_pid=None)
        )

        assert changed is True
        assert setting.bind_host == "127.0.0.1"
        assert setting.bind_port == 18180
        assert setting.auth.enabled is True
        assert setting.auth.token == "secret"
        update_setting.assert_called_once()

    def test_same_auth_values_do_not_persist(self, monkeypatch):
        setting = self._make_setting(bind_host="127.0.0.1", bind_port=18180, auth_enabled=True, token="secret")
        app_config = SimpleNamespace(setting=setting)
        monkeypatch.setattr(desktopEntry.configUtil, "get_app_config", lambda: app_config)
        update_setting = MagicMock()
        monkeypatch.setattr(desktopEntry.configUtil, "update_setting", update_setting)

        changed = desktopEntry._apply_desktop_overrides(
            desktopEntry.DesktopParams(port=18180, token="secret", parent_pid=None)
        )

        assert changed is False
        update_setting.assert_not_called()


class TestStdinProbe:
    def test_eof_triggers_shutdown(self):
        called = threading.Event()
        thread = desktopEntry._install_stdin_probe(
            on_eof=called.set,
            stream=io.StringIO(""),
        )
        thread.join(timeout=2)
        assert called.is_set()
