"""i18n 工具函数：展示名解析和多语言文本解析。"""
from __future__ import annotations

from util import configUtil

DEFAULT_LANG = "zh-CN"


def resolve_display_name(
    entity_name: str,
    i18n: dict | None,
    *,
    field: str = "display_name",
    lang: str | None = None,
) -> str:
    """从 i18n 数据中解析当前语言的展示名。

    优先级：i18n[field][当前语言] → i18n[field][默认语言] → entity_name
    """
    if not i18n:
        return entity_name
    i18n_text = i18n.get(field)
    if not i18n_text or not isinstance(i18n_text, dict):
        return entity_name
    effective_lang = lang or configUtil.get_language()
    return i18n_text.get(effective_lang) or i18n_text.get(DEFAULT_LANG) or entity_name


def resolve_i18n_text(i18n_text: dict[str, str] | None, lang: str) -> str | None:
    """从 I18nText 中解析指定语言的文本，语言缺失时回退到默认语言。"""
    if not i18n_text or not isinstance(i18n_text, dict):
        return None
    return i18n_text.get(lang) or i18n_text.get(DEFAULT_LANG)
