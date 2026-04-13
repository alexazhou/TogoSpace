"""i18n 工具函数：展示名解析和多语言文本解析。"""
from __future__ import annotations

from util import configUtil

DEFAULT_LANG = "zh-CN"

# 托盘菜单多语言文案
TRAY_STRINGS: dict[str, dict[str, str]] = {
    "status_starting": {"zh-CN": "启动中…", "en": "Starting…"},
    "status_running": {"zh-CN": "运行中", "en": "Running"},
    "status_stopped": {"zh-CN": "已停止", "en": "Stopped"},
    "status_error": {"zh-CN": "启动失败: {e}", "en": "Failed to start: {e}"},
    "status_label": {"zh-CN": "状态: {s}", "en": "Status: {s}"},
    "open_web": {"zh-CN": "打开 Web 界面", "en": "Open Web UI"},
    "open_config_dir": {"zh-CN": "打开配置目录", "en": "Open Config Dir"},
    "reset_data": {"zh-CN": "重置数据", "en": "Reset Data"},
    "version": {"zh-CN": "版本: v{v}", "en": "Version: v{v}"},
    "quit": {"zh-CN": "退出", "en": "Quit"},
    "confirm_reset": {
        "zh-CN": "确定要重置所有数据吗？\n所有聊天室、成员、消息记录将被删除，此操作不可撤销。",
        "en": "Are you sure you want to reset all data?\nAll chat rooms, members, and messages will be deleted. This cannot be undone.",
    },
    "reset_done_title": {"zh-CN": "重置成功", "en": "Reset Complete"},
    "reset_done_body": {"zh-CN": "数据已清除，请重新启动程序。", "en": "Data cleared. Please restart the application."},
    "language_menu": {"zh-CN": "语言 / Language", "en": "Language / 语言"},
    "lang_zh": {"zh-CN": "简体中文", "en": "简体中文 (Chinese)"},
    "lang_en": {"zh-CN": "English", "en": "English"},
}


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


def resolve_i18n_text(i18n_text: dict[str, str] | None, lang: str, **kwargs) -> str | None:
    """从 I18nText 中解析指定语言的文本，语言缺失时回退到默认语言。支持格式化参数。"""
    if not i18n_text or not isinstance(i18n_text, dict):
        return None
    text = i18n_text.get(lang) or i18n_text.get(DEFAULT_LANG)
    if text and kwargs:
        text = text.format(**kwargs)
    return text


def tray_t(key: str, **kwargs) -> str:
    """托盘文案翻译。"""
    lang = configUtil.get_language() if configUtil.is_loaded() else DEFAULT_LANG
    text = resolve_i18n_text(TRAY_STRINGS.get(key), lang, **kwargs)
    return text or key
