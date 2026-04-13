"""i18n 工具函数。"""
from __future__ import annotations

import json
import os

import appPaths
from util import configUtil

DEFAULT_LANG = "zh-CN"

_i18n_cache: dict[str, dict[str, str]] = {}


def _load_i18n(lang: str) -> dict[str, str]:
    """加载指定语言的 i18n 文件，已加载则返回缓存。"""
    if lang in _i18n_cache:
        return _i18n_cache[lang]

    i18n_dir = os.path.join(appPaths.ASSETS_DIR, "i18n")
    i18n_file = os.path.join(i18n_dir, f"{lang}.json")

    if not os.path.exists(i18n_file):
        return {}

    with open(i18n_file, encoding="utf-8") as f:
        _i18n_cache[lang] = json.load(f)

    return _i18n_cache[lang]


def t(key: str, **kwargs) -> str:
    """通用文案翻译，从 assets/i18n/{lang}.json 加载。"""
    lang = configUtil.get_language() if configUtil.is_loaded() else DEFAULT_LANG
    i18n_data = _load_i18n(lang)

    text = i18n_data.get(key)

    if text is None and lang != DEFAULT_LANG:
        i18n_data = _load_i18n(DEFAULT_LANG)
        text = i18n_data.get(key)

    if text and kwargs:
        text = text.format(**kwargs)

    return text or key


def extract_i18n_str(i18n_dict: dict | None, default: str | None = None, lang: str | None = None) -> str | None:
    """从 {lang: text} 结构提取指定语言的字符串。"""
    if not i18n_dict:
        return default

    effective_lang = lang or configUtil.get_language()

    return i18n_dict.get(effective_lang) or i18n_dict.get(DEFAULT_LANG) or default