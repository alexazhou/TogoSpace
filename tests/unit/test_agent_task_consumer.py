"""AgentTaskConsumer 单元测试：测试任务消费逻辑。"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from constants import AgentActivityStatus, AgentActivityType, AgentStatus, AgentTaskStatus
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtAgentTask import GtAgentTask
from service.agentService.agentTaskConsumer import AgentTaskConsumer
from util.assertUtil import MakeSureException


@pytest.fixture
def mock_gt_agent():
    gt_agent = MagicMock(spec=GtAgent)
    gt_agent.id = 1
    return gt_agent


@pytest.fixture
def mock_turn_runner():
    turn_runner = MagicMock()
    turn_runner.run_chat_turn = AsyncMock()
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
    with patch("service.agentService.agentTaskConsumer.gtAgentTaskManager") as mock_manager:
        mock_manager.get_first_unfinish_task = AsyncMock(return_value=None)
        mock_manager.has_consumable_task = AsyncMock(return_value=False)

        await consumer.consume()

        mock_manager.get_first_unfinish_task.assert_called_once_with(mock_gt_agent.id)
        mock_turn_runner.run_chat_turn.assert_not_called()


@pytest.mark.asyncio
async def test_consume_processes_pending_task(consumer, mock_gt_agent, mock_turn_runner):
    pending_task = MagicMock(spec=GtAgentTask)
    pending_task.id = 100
    pending_task.status = AgentTaskStatus.PENDING
    pending_task.task_data = {"room_id": 1}

    running_task = MagicMock(spec=GtAgentTask)
    running_task.id = 100
    running_task.status = AgentTaskStatus.RUNNING
    running_task.task_data = {"room_id": 1}

    with patch("service.agentService.agentTaskConsumer.gtAgentTaskManager") as mock_manager:
        mock_manager.get_first_unfinish_task = AsyncMock(side_effect=[pending_task, None])
        mock_manager.transition_task_status = AsyncMock(return_value=running_task)
        mock_manager.update_task_status = AsyncMock()
        mock_manager.has_consumable_task = AsyncMock(return_value=False)

        await consumer.consume()

        mock_manager.transition_task_status.assert_called_once_with(100, AgentTaskStatus.PENDING, AgentTaskStatus.RUNNING)
        mock_turn_runner.run_chat_turn.assert_called_once()
        mock_manager.update_task_status.assert_called_once_with(100, AgentTaskStatus.COMPLETED)


@pytest.mark.asyncio
async def test_consume_stops_on_failed_task(consumer, mock_gt_agent, mock_turn_runner):
    pending_task = MagicMock(spec=GtAgentTask)
    pending_task.id = 100
    pending_task.status = AgentTaskStatus.PENDING
    pending_task.task_data = {"room_id": 1}

    running_task = MagicMock(spec=GtAgentTask)
    running_task.id = 100
    running_task.status = AgentTaskStatus.RUNNING
    running_task.task_data = {"room_id": 1}

    with patch("service.agentService.agentTaskConsumer.gtAgentTaskManager") as mock_manager:
            mock_manager.get_first_unfinish_task = AsyncMock(return_value=pending_task)
            mock_manager.transition_task_status = AsyncMock(return_value=running_task)
            mock_manager.update_task_status = AsyncMock()
            mock_manager.has_consumable_task = AsyncMock(return_value=False)

            mock_turn_runner.run_chat_turn = AsyncMock(side_effect=RuntimeError("inference failed"))

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
    running_task = MagicMock(spec=GtAgentTask)
    running_task.id = 101
    running_task.status = AgentTaskStatus.RUNNING
    running_task.task_data = {"room_id": 42}

    with patch("service.agentService.agentTaskConsumer.gtAgentTaskManager") as mock_manager:
        mock_manager.get_first_unfinish_task = AsyncMock(return_value=running_task)
        mock_manager.update_task_status = AsyncMock()

        mock_turn_runner.run_chat_turn = AsyncMock(side_effect=RuntimeError("retry failed"))

        await consumer.consume()

        mock_turn_runner.run_chat_turn.assert_called_once_with(running_task)
        mock_manager.update_task_status.assert_called_once_with(101, AgentTaskStatus.FAILED, error_message="retry failed")
        assert consumer.status == AgentStatus.FAILED
        assert consumer._mock_activity_service.add_activity.await_args_list[-1].kwargs == {
            "gt_agent": mock_gt_agent,
            "activity_type": AgentActivityType.AGENT_STATE,
            "status": AgentActivityStatus.SUCCEEDED,
            "detail": AgentStatus.FAILED.name,
            "error_message": "retry failed",
        }


@pytest.mark.asyncio
async def test_resume_failed_raises_when_no_failed_task(consumer):
    with patch("service.agentService.agentTaskConsumer.gtAgentTaskManager") as mock_manager:
        mock_manager.get_first_unfinish_task = AsyncMock(return_value=None)

        with pytest.raises(MakeSureException, match="no failed task to resume"):
            await consumer.resume_failed()


@pytest.mark.asyncio
async def test_resume_failed_starts_consumer_with_resumed_task(consumer):
    failed_task = MagicMock(spec=GtAgentTask)
    failed_task.id = 100
    failed_task.status = AgentTaskStatus.FAILED
    failed_task.task_data = {"room_id": 42}

    resumed_task = MagicMock(spec=GtAgentTask)
    resumed_task.id = 100
    resumed_task.status = AgentTaskStatus.RUNNING
    resumed_task.task_data = {"room_id": 42}

    with patch("service.agentService.agentTaskConsumer.gtAgentTaskManager") as mock_manager:
        mock_manager.get_first_unfinish_task = AsyncMock(return_value=failed_task)
        mock_manager.transition_task_status = AsyncMock(return_value=resumed_task)

        with patch.object(consumer, "start") as mock_start:
            await consumer.resume_failed()

            mock_manager.transition_task_status.assert_called_once_with(100, AgentTaskStatus.FAILED, AgentTaskStatus.RUNNING)
            mock_start.assert_called_once()


@pytest.mark.asyncio
async def test_consume_marks_failed_when_first_task_is_failed(consumer, mock_gt_agent, mock_turn_runner):
    failed_task = MagicMock(spec=GtAgentTask)
    failed_task.id = 100
    failed_task.status = AgentTaskStatus.FAILED
    failed_task.task_data = {"room_id": 1}
    failed_task.error_message = "blocked by failed task"

    with patch("service.agentService.agentTaskConsumer.gtAgentTaskManager") as mock_manager:
        mock_manager.get_first_unfinish_task = AsyncMock(return_value=failed_task)

        await consumer.consume()

        mock_turn_runner.run_chat_turn.assert_not_called()
        assert consumer.status == AgentStatus.FAILED
        assert consumer._mock_activity_service.add_activity.await_args_list[0].kwargs == {
            "gt_agent": mock_gt_agent,
            "activity_type": AgentActivityType.AGENT_STATE,
            "status": AgentActivityStatus.SUCCEEDED,
            "detail": AgentStatus.ACTIVE.name,
            "error_message": None,
        }
        assert consumer._mock_activity_service.add_activity.await_args_list[-1].kwargs == {
            "gt_agent": mock_gt_agent,
            "activity_type": AgentActivityType.AGENT_STATE,
            "status": AgentActivityStatus.SUCCEEDED,
            "detail": AgentStatus.FAILED.name,
            "error_message": "blocked by failed task",
        }


@pytest.mark.asyncio
async def test_consume_keeps_failed_when_already_failed_and_first_task_is_failed(consumer, mock_turn_runner):
    failed_task = MagicMock(spec=GtAgentTask)
    failed_task.id = 100
    failed_task.status = AgentTaskStatus.FAILED
    failed_task.task_data = {"room_id": 1}
    failed_task.error_message = "blocked by failed task"
    consumer.status = AgentStatus.FAILED

    with patch("service.agentService.agentTaskConsumer.gtAgentTaskManager") as mock_manager:
        mock_manager.get_first_unfinish_task = AsyncMock(return_value=failed_task)

        await consumer.consume()

        mock_turn_runner.run_chat_turn.assert_not_called()
        assert consumer.status == AgentStatus.FAILED
        assert [call.kwargs["detail"] for call in consumer._mock_activity_service.add_activity.await_args_list] == [
            AgentStatus.ACTIVE.name,
            AgentStatus.FAILED.name,
        ]


@pytest.mark.asyncio
async def test_consume_auto_continues_when_pending_after_completion(consumer, mock_turn_runner):
    pending_task = MagicMock(spec=GtAgentTask)
    pending_task.id = 100
    pending_task.status = AgentTaskStatus.PENDING
    pending_task.task_data = {"room_id": 1}

    running_task = MagicMock(spec=GtAgentTask)
    running_task.id = 100
    running_task.status = AgentTaskStatus.RUNNING
    running_task.task_data = {"room_id": 1}

    # 模拟当前协程任务，使 finally 逻辑能正确执行
    mock_task = MagicMock()
    consumer._aio_consumer_task = mock_task

    with patch("service.agentService.agentTaskConsumer.gtAgentTaskManager") as mock_manager:
        with patch("asyncio.current_task", return_value=mock_task):
            mock_manager.get_first_unfinish_task = AsyncMock(side_effect=[pending_task, None])
            mock_manager.transition_task_status = AsyncMock(return_value=running_task)
            mock_manager.update_task_status = AsyncMock()
            mock_manager.has_consumable_task = AsyncMock(return_value=True)

            with patch.object(consumer, "start") as mock_start:
                await consumer.consume()

                mock_start.assert_called_once()
