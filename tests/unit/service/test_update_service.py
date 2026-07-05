"""updateService 单元测试：版本解析、比较、缓存、GitHub 请求。"""

import asyncio
import json
import time
from unittest import mock

import pytest

# 确保 src 在 Python 路径中
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../src"))

import service.updateService as updateService
from service.updateService import (
    _parse_version,
    is_newer_version,
    check_for_update,
    _CACHE_TTL_SECONDS,
)

def _mock_get_app_config():
    """返回一个 mock AppConfig，dev.latest_release 为空。"""
    from util.configTypes import AppConfig, SettingConfig, DevConfig
    app_config = AppConfig(setting=SettingConfig(version="v2"))
    return app_config

@pytest.fixture(autouse=True)
def mock_version(monkeypatch):
    monkeypatch.setattr(updateService, "__version__", "0.3.8")


# ───────────────────── _parse_version ─────────────────────

class TestParseVersion:
    """_parse_version 将版本字符串解析为可比较的元组。"""

    def test_standard_version(self):
        assert _parse_version("0.3.8") == (0, 3, 8)

    def test_version_with_v_prefix(self):
        assert _parse_version("v0.3.8") == (0, 3, 8)

    def test_version_with_uppercase_v(self):
        assert _parse_version("V1.2.3") == (1, 2, 3)

    def test_version_with_leading_whitespace(self):
        assert _parse_version("  v1.0.0") == (1, 0, 0)

    def test_version_with_trailing_junk(self):
        """带后缀如 -beta 时只取前三段。"""
        assert _parse_version("1.2.3-beta") == (1, 2, 3)

    def test_invalid_version_returns_zeros(self):
        assert _parse_version("abc") == (0, 0, 0)

    def test_empty_string_returns_zeros(self):
        assert _parse_version("") == (0, 0, 0)

    def test_two_part_version_returns_zeros(self):
        """只有两段的版本号不匹配 x.y.z 模式。"""
        assert _parse_version("1.2") == (0, 0, 0)

    def test_large_numbers(self):
        assert _parse_version("10.20.30") == (10, 20, 30)


# ───────────────────── is_newer_version ─────────────────────

class TestIsNewerVersion:
    """is_newer_version 判断 latest 是否比 current 更新。"""

    def test_patch_bump(self):
        assert is_newer_version("0.3.9", "0.3.8") is True

    def test_minor_bump(self):
        assert is_newer_version("0.4.0", "0.3.8") is True

    def test_major_bump(self):
        assert is_newer_version("1.0.0", "0.3.8") is True

    def test_same_version(self):
        assert is_newer_version("0.3.8", "0.3.8") is False

    def test_older_version(self):
        assert is_newer_version("0.3.7", "0.3.8") is False

    def test_with_v_prefix(self):
        assert is_newer_version("v0.3.9", "v0.3.8") is True

    def test_mixed_prefix(self):
        assert is_newer_version("v0.3.9", "0.3.8") is True

    def test_invalid_latest_returns_false(self):
        assert is_newer_version("invalid", "0.3.8") is False

    def test_invalid_current_makes_comparison_biased(self):
        """current 无效时解析为 (0,0,0)，任何有效 latest 都大于它。"""
        assert is_newer_version("0.0.1", "invalid") is True


# ───────────────────── check_for_update (缓存逻辑) ─────────────────────

class TestCheckForUpdateCache:
    """check_for_update 的缓存行为。"""

    def setup_method(self):
        """每个测试前重置全局缓存。"""
        updateService._cached_result = None
        updateService._cached_at = 0.0

    def teardown_method(self):
        """每个测试后重置全局缓存。"""
        updateService._cached_result = None
        updateService._cached_at = 0.0

    @pytest.mark.asyncio
    async def test_first_call_fetches_from_github(self):
        """首次调用应请求 GitHub API。"""
        mock_response = mock.MagicMock()
        mock_response.code = 200
        mock_response.body = json.dumps({
            "tag_name": "v99.0.0",
            "html_url": "https://github.com/test",
            "body": "release notes",
        }).encode()

        with mock.patch("util.configUtil.get_app_config", side_effect=_mock_get_app_config):
            with mock.patch("tornado.httpclient.AsyncHTTPClient") as mock_client_cls:
                mock_client = mock.MagicMock()
                mock_client.fetch = mock.AsyncMock(return_value=mock_response)
                mock_client_cls.return_value = mock_client

                result = await check_for_update(force=False)

        assert result["has_update"] is True
        assert result["current_version"] == "0.3.8"
        assert result["latest_version"] == "99.0.0"
        assert result["release_url"] == "https://github.com/test"
        assert result["release_notes"] == "release notes"
        mock_client.fetch.assert_called_once()

    @pytest.mark.asyncio
    async def test_second_call_uses_cache(self):
        """缓存未过期时不应再次请求 GitHub。"""
        # 预设缓存
        updateService._cached_result = {
            "has_update": True,
            "current_version": "0.3.8",
            "latest_version": "99.0.0",
            "release_url": "",
            "release_notes": "",
        }
        updateService._cached_at = time.time()

        with mock.patch("util.configUtil.get_app_config", side_effect=_mock_get_app_config):
            with mock.patch("tornado.httpclient.AsyncHTTPClient") as mock_client_cls:
                mock_client = mock.MagicMock()
                mock_client.fetch = mock.AsyncMock()
                mock_client_cls.return_value = mock_client

                result = await check_for_update(force=False)

        assert result["latest_version"] == "99.0.0"
        mock_client.fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_force_bypasses_cache(self):
        """force=True 应跳过缓存，强制请求 GitHub。"""
        updateService._cached_result = {
            "has_update": False,
            "current_version": "0.3.8",
            "latest_version": "0.3.8",
            "release_url": "",
            "release_notes": "",
        }
        updateService._cached_at = time.time()

        mock_response = mock.MagicMock()
        mock_response.code = 200
        mock_response.body = json.dumps({
            "tag_name": "v99.0.0",
            "html_url": "https://github.com/test",
            "body": "notes",
        }).encode()

        with mock.patch("util.configUtil.get_app_config", side_effect=_mock_get_app_config):
            with mock.patch("tornado.httpclient.AsyncHTTPClient") as mock_client_cls:
                mock_client = mock.MagicMock()
                mock_client.fetch = mock.AsyncMock(return_value=mock_response)
                mock_client_cls.return_value = mock_client

                result = await check_for_update(force=True)

        assert result["has_update"] is True
        assert result["latest_version"] == "99.0.0"
        mock_client.fetch.assert_called_once()

    @pytest.mark.asyncio
    async def test_expired_cache_refetches(self):
        """缓存过期后应重新请求 GitHub。"""
        updateService._cached_result = {
            "has_update": False,
            "current_version": "0.3.8",
            "latest_version": "0.3.8",
            "release_url": "",
            "release_notes": "",
        }
        # 设置缓存时间为很久以前
        updateService._cached_at = time.time() - _CACHE_TTL_SECONDS - 1

        mock_response = mock.MagicMock()
        mock_response.code = 200
        mock_response.body = json.dumps({
            "tag_name": "v99.0.0",
            "html_url": "https://github.com/test",
            "body": "",
        }).encode()

        with mock.patch("util.configUtil.get_app_config", side_effect=_mock_get_app_config):
            with mock.patch("tornado.httpclient.AsyncHTTPClient") as mock_client_cls:
                mock_client = mock.MagicMock()
                mock_client.fetch = mock.AsyncMock(return_value=mock_response)
                mock_client_cls.return_value = mock_client

                result = await check_for_update(force=False)

        assert result["latest_version"] == "99.0.0"
        mock_client.fetch.assert_called_once()


# ───────────────────── _fetch_github_release (错误处理) ─────────────────────

class TestFetchGithubRelease:
    """_fetch_github_release 的异常和边界处理。"""

    def setup_method(self):
        updateService._cached_result = None
        updateService._cached_at = 0.0

    def teardown_method(self):
        updateService._cached_result = None
        updateService._cached_at = 0.0

    @pytest.mark.asyncio
    async def test_non_200_returns_fallback(self):
        """GitHub API 返回非 200 时返回 fallback。"""
        mock_response = mock.MagicMock()
        mock_response.code = 403

        with mock.patch("util.configUtil.get_app_config", side_effect=_mock_get_app_config):
            with mock.patch("tornado.httpclient.AsyncHTTPClient") as mock_client_cls:
                mock_client = mock.MagicMock()
                mock_client.fetch = mock.AsyncMock(return_value=mock_response)
                mock_client_cls.return_value = mock_client

                result = await check_for_update(force=True)

        assert result["has_update"] is False
        assert result["current_version"] == result["latest_version"]

    @pytest.mark.asyncio
    async def test_network_error_returns_fallback(self):
        """网络异常时返回 fallback，不抛出。"""
        with mock.patch("util.configUtil.get_app_config", side_effect=_mock_get_app_config):
            with mock.patch("tornado.httpclient.AsyncHTTPClient") as mock_client_cls:
                mock_client = mock.MagicMock()
                mock_client.fetch = mock.AsyncMock(side_effect=Exception("timeout"))
                mock_client_cls.return_value = mock_client

                result = await check_for_update(force=True)

        assert result["has_update"] is False

    @pytest.mark.asyncio
    async def test_release_notes_truncated(self):
        """release notes 超过 2000 字符时截断。"""
        long_body = "x" * 3000
        mock_response = mock.MagicMock()
        mock_response.code = 200
        mock_response.body = json.dumps({
            "tag_name": "v0.3.8",
            "html_url": "https://github.com/test",
            "body": long_body,
        }).encode()

        with mock.patch("util.configUtil.get_app_config", side_effect=_mock_get_app_config):
            with mock.patch("tornado.httpclient.AsyncHTTPClient") as mock_client_cls:
                mock_client = mock.MagicMock()
                mock_client.fetch = mock.AsyncMock(return_value=mock_response)
                mock_client_cls.return_value = mock_client

                result = await check_for_update(force=True)

        assert len(result["release_notes"]) == 2000

    @pytest.mark.asyncio
    async def test_no_update_when_same_version(self):
        """GitHub tag 与当前版本相同时 has_update=False。"""
        mock_response = mock.MagicMock()
        mock_response.code = 200
        mock_response.body = json.dumps({
            "tag_name": "v0.3.8",
            "html_url": "https://github.com/test",
            "body": "",
        }).encode()

        with mock.patch("util.configUtil.get_app_config", side_effect=_mock_get_app_config):
            with mock.patch("tornado.httpclient.AsyncHTTPClient") as mock_client_cls:
                mock_client = mock.MagicMock()
                mock_client.fetch = mock.AsyncMock(return_value=mock_response)
                mock_client_cls.return_value = mock_client

                result = await check_for_update(force=True)

        assert result["has_update"] is False
        assert result["latest_version"] == "0.3.8"

    @pytest.mark.asyncio
    async def test_empty_body_returns_empty_notes(self):
        """body 为空时 release_notes 为空字符串。"""
        mock_response = mock.MagicMock()
        mock_response.code = 200
        mock_response.body = json.dumps({
            "tag_name": "v0.3.8",
            "html_url": "https://github.com/test",
            "body": None,
        }).encode()

        with mock.patch("util.configUtil.get_app_config", side_effect=_mock_get_app_config):
            with mock.patch("tornado.httpclient.AsyncHTTPClient") as mock_client_cls:
                mock_client = mock.MagicMock()
                mock_client.fetch = mock.AsyncMock(return_value=mock_response)
                mock_client_cls.return_value = mock_client

                result = await check_for_update(force=True)

        assert result["release_notes"] == ""
