"""TUI 国际化支持：翻译字典与 t() 工具函数。"""
from __future__ import annotations

_LANG = "zh-CN"

_TRANSLATIONS: dict[str, dict[str, str]] = {
    # 面板标题
    "chat_rooms": {"zh-CN": "聊天室", "en": "Chat Rooms"},
    "team_members": {"zh-CN": "团队成员", "en": "Team Members"},

    # Agent 状态
    "status_busy": {"zh-CN": "忙碌", "en": "Busy"},
    "status_failed": {"zh-CN": "失败", "en": "Failed"},
    "status_idle": {"zh-CN": "空闲", "en": "Idle"},

    # 房间列表
    "no_messages": {"zh-CN": "暂无消息", "en": "No messages"},
    "n_members": {"zh-CN": "{n}人", "en": "{n} members"},
    "select_a_room": {"zh-CN": "请选择一个房间", "en": "Select a room"},
    "room_not_found": {"zh-CN": "房间不存在", "en": "Room not found"},
    "no_members": {"zh-CN": "该房间暂无成员", "en": "No members in this room"},
    "no_room_selected": {"zh-CN": "暂无选中房间", "en": "No room selected"},
    "unread_suffix": {"zh-CN": "未读", "en": "unread"},

    # 连接状态栏
    "connected": {"zh-CN": "已连接", "en": "Connected"},
    "disconnected": {"zh-CN": "已断开", "en": "Disconnected"},
    "reconnecting": {"zh-CN": "重连中…", "en": "Reconnecting…"},
    "reconnect_countdown": {"zh-CN": "已断开，{n}s 后重连", "en": "Disconnected, reconnecting in {n}s"},
    "message_count": {"zh-CN": "消息数: {n}", "en": "Messages: {n}"},

    # 工作状态
    "processing": {"zh-CN": "处理中…", "en": "Processing…"},

    # 输入区
    "input_placeholder": {"zh-CN": "在此输入消息...", "en": "Type a message..."},
    "observer_mode": {"zh-CN": "当前为观察模式", "en": "Observer mode"},

    # 快捷键描述
    "keybind_quit": {"zh-CN": "退出", "en": "Quit"},
    "keybind_prev_room": {"zh-CN": "上一个房间", "en": "Previous room"},
    "keybind_next_room": {"zh-CN": "下一个房间", "en": "Next room"},
    "keybind_select_room": {"zh-CN": "切换到当前房间", "en": "Switch to room"},
    "keybind_focus_input": {"zh-CN": "进入输入模式", "en": "Focus input"},

    # 系统消息
    "backend_not_ready": {
        "zh-CN": (
            "⚠ 当前未配置大模型服务\n\n"
            "请通过以下方式完成配置：\n"
            "1. 手动编辑 ~/.team_agent/setting.json\n"
            "2. 通过 Web Console 完成配置\n\n"
            "Web Console 地址：{url}"
        ),
        "en": (
            "⚠ No LLM service configured\n\n"
            "Please complete the configuration:\n"
            "1. Manually edit ~/.team_agent/setting.json\n"
            "2. Use the Web Console\n\n"
            "Web Console: {url}"
        ),
    },
    "backend_unreachable": {
        "zh-CN": "无法连接到后端服务，请检查服务是否已启动。",
        "en": "Cannot connect to backend. Please check if the service is running.",
    },
    "load_messages_failed": {
        "zh-CN": "加载消息失败，请检查网络连接。",
        "en": "Failed to load messages. Please check your network connection.",
    },
    "send_failed": {"zh-CN": "消息发送失败", "en": "Failed to send message"},

    # 退出确认
    "quit_confirm_title": {"zh-CN": "想退出吗？", "en": "Quit?"},
    "quit_confirm_body": {
        "zh-CN": "按 [bold]Ctrl+Q[/bold] 退出程序",
        "en": "Press [bold]Ctrl+Q[/bold] to quit",
    },
}


def set_language(lang: str) -> None:
    """设置当前 TUI 显示语言。"""
    global _LANG
    _LANG = lang


def get_language() -> str:
    """返回当前 TUI 语言。"""
    return _LANG


def t(key: str, **kwargs: object) -> str:
    """翻译指定 key，支持 {placeholder} 格式替换。"""
    entry = _TRANSLATIONS.get(key)
    if entry is None:
        return key
    text = entry.get(_LANG) or entry.get("zh-CN") or key
    if kwargs:
        text = text.format(**kwargs)
    return text
