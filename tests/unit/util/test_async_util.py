"""测试 asyncUtil.cancel_task_safely 函数。"""
import asyncio
import pytest


class TestCancelTaskSafely:
    """测试安全取消 asyncio task 的各种场景。"""

    def test_cancel_none_task(self):
        """传入 None 时应静默返回，不抛异常。"""
        from util.asyncUtil import cancel_task_safely

        cancel_task_safely(None)

    def test_cancel_done_task(self):
        """已完成的 task 应静默返回，不调用 cancel。"""
        from util.asyncUtil import cancel_task_safely

        async def dummy():
            return 42

        loop = asyncio.new_event_loop()
        task = loop.create_task(dummy())
        loop.run_until_complete(task)

        # task 已完成，调用应静默返回
        cancel_task_safely(task)
        # 验证 task 状态未被改变（仍然是 done）
        assert task.done()
        assert task.result() == 42

        loop.close()

    def test_cancel_active_task(self):
        """活跃的 task 应被正确取消。"""
        from util.asyncUtil import cancel_task_safely

        async def long_running():
            await asyncio.sleep(10)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        task = loop.create_task(long_running())

        # task 未完成，应被取消
        cancel_task_safely(task)

        # 运行 loop 处理取消，task 才会真正进入 cancelled 状态
        with pytest.raises(asyncio.CancelledError):
            loop.run_until_complete(task)

        assert task.cancelled()
        loop.close()

    def test_cancel_task_with_closed_loop(self):
        """loop 已关闭时应静默返回，不抛异常。"""
        from util.asyncUtil import cancel_task_safely

        loop = asyncio.new_event_loop()
        # 先创建并完成一个 task，然后关闭 loop
        async def dummy():
            return 1

        task = loop.create_task(dummy())
        loop.run_until_complete(task)  # 确保 coroutine 被 await
        loop.close()

        # task 已完成且 loop 已关闭，应静默返回
        cancel_task_safely(task)

    def test_cancel_task_runtime_error(self):
        """get_loop() 抛 RuntimeError 时应静默返回。"""
        from util.asyncUtil import cancel_task_safely
        from unittest import mock

        # 创建一个 mock task，让 get_loop() 抛 RuntimeError
        mock_task = mock.MagicMock(spec=asyncio.Task)
        mock_task.done.return_value = False
        mock_task.get_loop.side_effect = RuntimeError("task 已 detach")

        cancel_task_safely(mock_task)
        # 未调用 cancel
        mock_task.cancel.assert_not_called()

    def test_cancel_task_then_get_loop_raises(self):
        """get_loop() 成功但 is_closed() 抛异常时的处理。"""
        from util.asyncUtil import cancel_task_safely
        from unittest import mock

        mock_task = mock.MagicMock(spec=asyncio.Task)
        mock_task.done.return_value = False
        mock_loop = mock.MagicMock(spec=asyncio.AbstractEventLoop)
        mock_loop.is_closed.side_effect = RuntimeError("loop 状态异常")
        mock_task.get_loop.return_value = mock_loop

        cancel_task_safely(mock_task)
        # 异常被捕获，未调用 cancel
        mock_task.cancel.assert_not_called()