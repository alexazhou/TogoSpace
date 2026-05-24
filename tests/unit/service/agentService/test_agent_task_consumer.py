"""AgentTaskConsumer 单元测试：测试任务消费逻辑。"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from constants import AgentActivityStatus, AgentActivityType, AgentStatus, AgentTaskStatus, AgentTaskType
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtScheculeTask import GtScheculeTask
from service.agentService.agentTaskConsumer import AgentTaskConsumer

@pytest.fixture
def mock_gt_agent():
    gt_agent = MagicMock(spec=GtAgent)
    gt_agent.id = 1
    return gt_agent


@pytest.fixture
def mock_turn_runner():
    turn_runner = MagicMock()
    turn_runner.run_task_turn = AsyncMock()
    return turn_runner


@pytest.fixture
def consumer(mock_gt_agent, mock_turn_runner):
    with patch("service.agentService.agentTaskConsumer.AgentTurnRunner", return_value=mock_turn_runner):
        with patch("service.agentService.agentTaskConsumer.agentActivityService") as mock_activity_svc:
            mock_activity_svc.add_activity = AsyncMock()
            c = AgentTaskConsumer(gt_agent=mock_gt_agent, system_prompt="test")
            c._mock_activity_service = mock_activity_svc
            yield c


@pytest.mark.asyncio
async def test_consume_no_task_returns_early(consumer, mock_gt_agent, mock_turn_runner):
    with patch("service.agentService.agentTaskConsumer.gtScheculeTaskManager") as mock_manager:
        mock_manager.get_first_unfinish_task = AsyncMock(return_value=None)
        mock_manager.has_consumable_task = AsyncMock(return_value=False)

        await consumer.consume()

        mock_manager.get_first_unfinish_task.assert_called_once_with(mock_gt_agent.id)
        mock_turn_runner.run_task_turn.assert_not_called()



@pytest.mark.asyncio
async def test_consume_stops_on_failed_task(consumer, mock_gt_agent, mock_turn_runner):
    pending_task = MagicMock(spec=GtScheculeTask)
    pending_task.id = 100
    pending_task.status = AgentTaskStatus.PENDING
    pending_task.task_data = {"room_id": 1}

    running_task = MagicMock(spec=GtScheculeTask)
    running_task.id = 100
    running_task.status = AgentTaskStatus.RUNNING
    running_task.task_data = {"room_id": 1}

    with patch("service.agentService.agentTaskConsumer.gtScheculeTaskManager") as mock_manager:
            mock_manager.get_first_unfinish_task = AsyncMock(return_value=pending_task)
            mock_manager.transition_task_status = AsyncMock(return_value=running_task)
            mock_manager.update_task_status = AsyncMock()
            mock_manager.has_consumable_task = AsyncMock(return_value=False)

            mock_turn_runner.run_task_turn = AsyncMock(side_effect=RuntimeError("inference failed"))

            await consumer.consume()

            assert consumer.status == AgentStatus.FAILED
            mock_manager.update_task_status.assert_called_once_with(100, AgentTaskStatus.FAILED, error_message="inference failed")
            assert consumer._mock_activity_service.add_activity.await_args_list[-1].kwargs == {
                "gt_agent": mock_gt_agent,
                "activity_type": AgentActivityType.AGENT_STATE,
                "status": AgentActivityStatus.SUCCEEDED,
                "detail": AgentStatus.FAILED.name,
                "error_message": "inference failed",
            }


@pytest.mark.asyncio
async def test_consume_running_task_retries_and_keeps_failed_status_on_error(consumer, mock_gt_agent, mock_turn_runner):
    running_task = MagicMock(spec=GtScheculeTask)
    running_task.id = 101
    running_task.status = AgentTaskStatus.RUNNING
    running_task.task_data = {"room_id": 42}

    with patch("service.agentService.agentTaskConsumer.gtScheculeTaskManager") as mock_manager:
        mock_manager.get_first_unfinish_task = AsyncMock(return_value=running_task)
        mock_manager.update_task_status = AsyncMock()

        mock_turn_runner.run_task_turn = AsyncMock(side_effect=RuntimeError("retry failed"))

        await consumer.consume()

        mock_turn_runner.run_task_turn.assert_called_once_with(running_task)
        mock_manager.update_task_status.assert_called_once_with(101, AgentTaskStatus.FAILED, error_message="retry failed")
        assert consumer.status == AgentStatus.FAILED
        assert consumer._mock_activity_service.add_activity.await_args_list[-1].kwargs == {
            "gt_agent": mock_gt_agent,
            "activity_type": AgentActivityType.AGENT_STATE,
            "status": AgentActivityStatus.SUCCEEDED,
            "detail": AgentStatus.FAILED.name,
            "error_message": "retry failed",
        }




# ─── cancel_current_turn 相关测试 ────────────────────────────


def test_cancel_current_turn_returns_false_when_not_active(consumer):
    """非 ACTIVE 状态时，cancel_current_turn 返回 False。"""
    consumer.status = AgentStatus.IDLE
    assert consumer.cancel_current_turn() is False

    consumer.status = AgentStatus.FAILED
    assert consumer.cancel_current_turn() is False


def test_cancel_current_turn_returns_false_when_no_consumer_task(consumer):
    """ACTIVE 但无消费协程时，cancel_current_turn 返回 False。"""
    consumer.status = AgentStatus.ACTIVE
    consumer._aio_consumer_task = None
    assert consumer.cancel_current_turn() is False


def test_cancel_current_turn_returns_false_when_task_already_done(consumer):
    """ACTIVE 但协程已结束时，cancel_current_turn 返回 False。"""
    consumer.status = AgentStatus.ACTIVE
    done_task = MagicMock()
    done_task.done.return_value = True
    consumer._aio_consumer_task = done_task
    assert consumer.cancel_current_turn() is False


def test_cancel_current_turn_sets_flag_and_cancels_task(consumer):
    """正常情况下，cancel_current_turn 设置 _cancel_requested 并取消 Task。"""
    consumer.status = AgentStatus.ACTIVE
    mock_task = MagicMock()
    mock_task.done.return_value = False
    consumer._aio_consumer_task = mock_task

    result = consumer.cancel_current_turn()

    assert result is True
    assert consumer._cancel_requested is True
    mock_task.cancel.assert_called_once()


@pytest.mark.asyncio
async def test_consume_handles_cancel_request(consumer, mock_gt_agent, mock_turn_runner):
    """人工取消：CancelledError + _cancel_requested=True → 执行 cancel 收尾逻辑。"""
    pending_task = MagicMock(spec=GtScheculeTask)
    pending_task.id = 200
    pending_task.status = AgentTaskStatus.PENDING

    running_task = MagicMock(spec=GtScheculeTask)
    running_task.id = 200
    running_task.status = AgentTaskStatus.RUNNING

    # 模拟 cancel_current_turn() 的行为：先设置标志，再引发 CancelledError
    def _simulate_cancel(*args, **kwargs):
        consumer._cancel_requested = True
        raise asyncio.CancelledError

    mock_turn_runner.run_task_turn = AsyncMock(side_effect=_simulate_cancel)
    mock_turn_runner.handle_cancel_turn = AsyncMock()

    with patch("service.agentService.agentTaskConsumer.gtScheculeTaskManager") as mock_manager:
        mock_manager.get_first_unfinish_task = AsyncMock(return_value=pending_task)
        mock_manager.transition_task_status = AsyncMock(return_value=running_task)
        mock_manager.update_task_status = AsyncMock()
        mock_manager.has_consumable_task = AsyncMock(return_value=False)

        await consumer.consume()

    # handle_cancel_turn 应被调用
    mock_turn_runner.handle_cancel_turn.assert_awaited_once()
    # task 应被标记为 CANCELLED
    mock_manager.update_task_status.assert_called_once_with(200, AgentTaskStatus.CANCELLED, error_message="cancelled by user")
    # 活动记录应包含 CANCELLED
    cancel_activity_calls = [
        call for call in consumer._mock_activity_service.add_activity.await_args_list
        if call.kwargs.get("status") == AgentActivityStatus.CANCELLED
    ]
    assert len(cancel_activity_calls) == 1
    # 最终状态应是 IDLE（不是 FAILED）
    assert consumer.status == AgentStatus.IDLE
    # flag 应已重置
    assert consumer._cancel_requested is False


@pytest.mark.asyncio
async def test_consume_reraises_cancelled_error_when_not_human_stop(consumer, mock_gt_agent, mock_turn_runner):
    """非人工取消的 CancelledError（如 hot reload）应原样 re-raise。"""
    pending_task = MagicMock(spec=GtScheculeTask)
    pending_task.id = 300
    pending_task.status = AgentTaskStatus.PENDING

    running_task = MagicMock(spec=GtScheculeTask)
    running_task.id = 300
    running_task.status = AgentTaskStatus.RUNNING

    mock_turn_runner.run_task_turn = AsyncMock(side_effect=asyncio.CancelledError)
    mock_turn_runner.handle_cancel_turn = AsyncMock()

    with patch("service.agentService.agentTaskConsumer.gtScheculeTaskManager") as mock_manager:
        mock_manager.get_first_unfinish_task = AsyncMock(return_value=pending_task)
        mock_manager.transition_task_status = AsyncMock(return_value=running_task)
        mock_manager.update_task_status = AsyncMock()

        # _cancel_requested 保持 False（默认）
        consumer._cancel_requested = False

        with pytest.raises(asyncio.CancelledError):
            await consumer.consume()

    # handle_cancel_turn 不应被调用
    mock_turn_runner.handle_cancel_turn.assert_not_awaited()
    # task 状态不应被更新（CancelledError 直接穿透）
    mock_manager.update_task_status.assert_not_called()


@pytest.mark.asyncio
async def test_consume_resets_cancel_flag_at_entry(consumer, mock_turn_runner):
    """consume() 入口应防御性重置 _cancel_requested。"""
    consumer._cancel_requested = True  # 残留的脏状态

    with patch("service.agentService.agentTaskConsumer.gtScheculeTaskManager") as mock_manager:
        mock_manager.get_first_unfinish_task = AsyncMock(return_value=None)
        mock_manager.has_consumable_task = AsyncMock(return_value=False)

        await consumer.consume()

    assert consumer._cancel_requested is False


# ─── 任务驱动型唤醒（TODO_TASK）相关测试 ────────────────────────────


@pytest.mark.asyncio
async def test_check_and_schedule_creates_collaboration_task(consumer, mock_gt_agent):
    """有活跃协作任务且无已有调度记录时，应自动创建 TODO_TASK 调度。"""
    mock_agent_task = MagicMock()
    mock_agent_task.id = 42
    mock_agent_task.title = "整理文档"

    with patch("service.agentService.agentTaskConsumer.gtScheculeTaskManager") as mock_sched_mgr:
        mock_sched_mgr.has_pending_collaboration_task = AsyncMock(return_value=False)
        mock_sched_mgr.create_task = AsyncMock()

        with patch("dal.db.gtAgentTaskManager.get_first_active_task", new=AsyncMock(return_value=mock_agent_task)):
            await consumer._check_and_schedule_collaboration_tasks()

        mock_sched_mgr.create_task.assert_awaited_once_with(
            mock_gt_agent.id,
            AgentTaskType.TODO_TASK,
            {"agent_task_id": 42},
        )


@pytest.mark.asyncio
async def test_check_and_schedule_skips_when_no_active_task(consumer):
    """无活跃协作任务时，不创建调度记录。"""
    with patch("service.agentService.agentTaskConsumer.gtScheculeTaskManager") as mock_sched_mgr:
        mock_sched_mgr.create_task = AsyncMock()

        with patch("dal.db.gtAgentTaskManager.get_first_active_task", new=AsyncMock(return_value=None)):
            await consumer._check_and_schedule_collaboration_tasks()

        mock_sched_mgr.create_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_and_schedule_idempotent_when_already_scheduled(consumer, mock_gt_agent):
    """已有 PENDING TODO_TASK 调度记录时，不重复创建（幂等）。"""
    mock_agent_task = MagicMock()
    mock_agent_task.id = 42
    mock_agent_task.title = "整理文档"

    with patch("service.agentService.agentTaskConsumer.gtScheculeTaskManager") as mock_sched_mgr:
        mock_sched_mgr.has_pending_collaboration_task = AsyncMock(return_value=True)
        mock_sched_mgr.create_task = AsyncMock()

        with patch("dal.db.gtAgentTaskManager.get_first_active_task", new=AsyncMock(return_value=mock_agent_task)):
            await consumer._check_and_schedule_collaboration_tasks()

        mock_sched_mgr.create_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_consume_triggers_collaboration_task_check_after_completion(consumer, mock_gt_agent, mock_turn_runner):
    """任务完成（COMPLETED）后，应调用 _check_and_schedule_collaboration_tasks。"""
    pending_task = MagicMock(spec=GtScheculeTask)
    pending_task.id = 100
    pending_task.status = AgentTaskStatus.PENDING
    pending_task.task_data = {"room_id": 1}

    running_task = MagicMock(spec=GtScheculeTask)
    running_task.id = 100
    running_task.status = AgentTaskStatus.RUNNING
    running_task.task_data = {"room_id": 1}

    with patch("service.agentService.agentTaskConsumer.gtScheculeTaskManager") as mock_manager:
        mock_manager.get_first_unfinish_task = AsyncMock(side_effect=[pending_task, None])
        mock_manager.transition_task_status = AsyncMock(return_value=running_task)
        mock_manager.update_task_status = AsyncMock()
        mock_manager.has_consumable_task = AsyncMock(return_value=False)

        with patch.object(consumer, "_check_and_schedule_collaboration_tasks", new=AsyncMock()) as mock_check:
            await consumer.consume()
            mock_check.assert_awaited_once()
