"""检查 GitHub 最新版本，内存缓存 12 小时。"""

import logging
import re
import time
from typing import Any

import tornado.httpclient

from util import configUtil
from version import __version__

logger = logging.getLogger(__name__)

_GITHUB_RELEASES_URL = "https://api.github.com/repos/alexazhou/TogoSpace/releases/latest"
_CACHE_TTL_SECONDS = 12 * 60 * 60  # 12 hours

_cached_result: dict[str, Any] | None = None
_cached_at: float = 0.0


def _parse_version(version_str: str) -> tuple[int, ...]:
    """将 'v0.3.8' 或 '0.3.8' 解析为可比较的元组。"""
    cleaned = version_str.strip().lstrip("vV")
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", cleaned)
    if not match:
        return (0, 0, 0)
    return tuple(int(x) for x in match.groups())


def is_newer_version(latest: str, current: str) -> bool:
    """判断 latest 是否比 current 更新。"""
    return _parse_version(latest) > _parse_version(current)


async def check_for_update(force: bool = False) -> dict[str, Any]:
    """检查是否有新版本可用。

    Args:
        force: 为 True 时跳过缓存，强制请求 GitHub。

    Returns:
        {
            "has_update": bool,
            "current_version": str,
            "latest_version": str,
            "release_url": str,
            "release_notes": str,
        }
    """
    global _cached_result, _cached_at

    # dev.latest_release 优先：手动指定版本号，跳过 GitHub API，方便测试更新 UI
    current = __version__
    setting = configUtil.get_app_config().setting
    dev_release = setting.dev.get("latest_release")
    if dev_release:
        return {
            "has_update": is_newer_version(str(dev_release), current),
            "current_version": current,
            "latest_version": str(dev_release).lstrip("v"),
            "release_url": "",
            "release_notes": "",
        }

    now = time.time()
    if not force and _cached_result is not None and (now - _cached_at) < _CACHE_TTL_SECONDS:
        return _cached_result

    result = await _fetch_github_release()
    _cached_result = result
    _cached_at = now
    return result


async def _fetch_github_release() -> dict[str, Any]:
    """从 GitHub API 获取最新 release 信息。"""
    current = __version__
    fallback = {
        "has_update": False,
        "current_version": current,
        "latest_version": current,
        "release_url": "",
        "release_notes": "",
    }

    try:
        http_client = tornado.httpclient.AsyncHTTPClient()
        request = tornado.httpclient.HTTPRequest(
            url=_GITHUB_RELEASES_URL,
            method="GET",
            headers={
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "TogoSpace-UpdateChecker",
            },
            request_timeout=10,
        )
        response = await http_client.fetch(request, raise_error=False)

        if response.code != 200:
            logger.warning("GitHub releases API returned %d", response.code)
            return fallback

        import json
        data = json.loads(response.body)
        tag_name = data.get("tag_name", "")
        html_url = data.get("html_url", "")
        body = data.get("body", "")

        has_update = is_newer_version(tag_name, current)

        return {
            "has_update": has_update,
            "current_version": current,
            "latest_version": tag_name.lstrip("v"),
            "release_url": html_url,
            "release_notes": body[:2000] if body else "",
        }
    except Exception as e:
        logger.warning("Failed to check for updates: %s", e)
        return fallback
