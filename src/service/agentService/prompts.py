"""非模板型提示词与交互常量定义。

大部分系统级与运行时交互提示词已迁移至 templates/ 目录下（由 Jinja2 模板引擎管理）。
本模块仅保留跨模块引用的少量独立文案常量及专用提示词。
"""

# 1. 房间普通轮次激活结束语 (被 promptBuilder / 模板作为兜底引用)
TURN_CONTEXT_SUFFIX = "你现在可以开始发言（send_chat_msg）或调用工具。在全部完成后，请务必调用 finish_action 结束行动。"

# 2. 协作任务处理 (TODO_TASK) 取消权限文案（被 tools.py 与任务通知共用）
TODO_TASK_CANCEL_INSTRUCTION = '- 若需取消，请调用 `update_task` 将状态改为 CANCELLED。'
TODO_TASK_NO_CANCEL_INSTRUCTION = (
    '- 你无权自行取消任务：若确实无法继续，请先调用 `update_task` 将状态改为 ON_HOLD 暂缓，'
    '并在房间说明原因，请创建人或验收人取消。'
)

# 3. 专用辅助提示词：多模态识图
VISION_RECOGNIZE_PROMPT = '''请描述这张图片的内容，包括文字、图表、界面元素等关键信息，供 agent 后续决策使用。'''
