import asyncio


def cancel_task_safely(task: asyncio.Task | None) -> None:
    """安全取消 asyncio task；任务为空、已结束或所属 loop 已关闭时静默返回。"""
    if task is None or task.done():
        return
    try:
        if task.get_loop().is_closed():
            return
        task.cancel()
    except RuntimeError:
        return
