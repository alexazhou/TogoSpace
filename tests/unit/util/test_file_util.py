"""测试 fileUtil.ensure_dir 函数。"""
import os
from unittest import mock

import pytest


class TestEnsureDir:
    """测试确保目录存在的各种场景。"""

    def test_existing_dir_is_noop(self, tmp_path):
        """目录已存在时应静默返回，不抛异常。"""
        from util.fileUtil import ensure_dir

        ensure_dir(str(tmp_path))
        assert tmp_path.is_dir()

    def test_creates_missing_dir(self, tmp_path):
        """目录不存在时应自动创建。"""
        from util.fileUtil import ensure_dir

        target = tmp_path / "new_dir"
        assert not target.exists()

        ensure_dir(str(target))

        assert target.is_dir()

    def test_creates_nested_dirs(self, tmp_path):
        """路径中存在多层缺失目录时应全部创建。"""
        from util.fileUtil import ensure_dir

        target = tmp_path / "a" / "b" / "c"
        assert not target.exists()

        ensure_dir(str(target))

        assert target.is_dir()

    def test_raises_togo_exception_on_os_error(self, tmp_path):
        """os.makedirs 抛出 OSError 时应转换为 TogoException。"""
        from exception import TogoException
        from util.fileUtil import ensure_dir

        target = tmp_path / "blocked"

        with mock.patch("os.makedirs", side_effect=OSError(13, "Permission denied")):
            with pytest.raises(TogoException) as exc_info:
                ensure_dir(str(target))

        assert exc_info.value.error_code == "directory_create_failed"
        assert "Permission denied" in exc_info.value.error_message

    def test_error_message_contains_path(self, tmp_path):
        """TogoException 的错误信息中应包含目标路径。"""
        from exception import TogoException
        from util.fileUtil import ensure_dir

        target = str(tmp_path / "no_access")

        with mock.patch("os.makedirs", side_effect=OSError(13, "Permission denied")):
            with pytest.raises(TogoException) as exc_info:
                ensure_dir(target)

        assert target in exc_info.value.error_message


class TestValidateAbsolutePath:
    """测试 validate_absolute_path 函数。"""

    def test_absolute_path_passes(self):
        """绝对路径应校验通过。"""
        from util.fileUtil import validate_absolute_path
        # Unix 风格
        validate_absolute_path("/tmp/test") 
        # Windows 风格（在 Unix 下 isabs 会返回 False，所以我们这里仅在对应系统下测试，或者通过 Mock）
        with mock.patch("os.path.isabs", return_value=True):
            validate_absolute_path("C:\\Users\\Desktop")

    def test_home_relative_path_passes(self):
        """以 ~ 开头的路径应校验通过。"""
        from util.fileUtil import validate_absolute_path
        validate_absolute_path("~/workspace/test")  # 不抛异常即为通过

    def test_relative_path_fails(self):
        """不满足绝对路径条件的路径应抛出 TogoException。"""
        from exception import TogoException
        from util.fileUtil import validate_absolute_path

        with pytest.raises(TogoException) as exc_info:
            validate_absolute_path("2222")

        assert exc_info.value.error_code == "invalid_path_format"
        assert "不支持相对路径" in exc_info.value.error_message
        assert "2222" in exc_info.value.error_message
