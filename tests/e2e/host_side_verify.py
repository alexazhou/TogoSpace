"""host 侧拆分验证：用 TSP read_image 真实结果走完整链路。

验证链路：TSP 返回 base64+元数据 → turn runner 写单条 TOOL 消息（图片挂附件）→
llmService 发送时拆分（TOOL 文本 + USER 图片消息 + data URL 组装）。
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src"))

from unittest.mock import AsyncMock, MagicMock

from constants import AgentHistoryStatus, AgentHistoryTag, DriverType, OpenaiApiRole, RoomState, TurnStepResult
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtAgentHistory import GtAgentHistory
from model.dbModel.agentMessage import AgentMessage, MessageAttachment
from service.agentService.agentTurnRunner import AgentTurnRunner
from service.agentService.toolRegistry import ToolExecutionResult
from service.agentService.driver.base import AgentDriverConfig
from service.roomService import ChatRoom
from util import llmApiUtil


def _fake_tsp_result():
    """从 TSP 真实返回的 read_image 结果（联调素材/test_image.png）。"""
    return {
        "file_path": "/Volumes/PDATA/GitDB/TeamAgent/dev_storage_root/workspace/软件研发团队/联调素材/test_image.png",
        "mime_type": "image/png",
        "format": "png",
        "width": 640,
        "height": 360,
        "size_bytes": 3216,
        "base64": "iVBORw0KGgoAAAANSUhEUgAAAoAAAAFoCAIAAABI",
    }


async def main():
    gt_agent = GtAgent(id=1, team_id=1, name="TestAgent", role_template_id=1, model="mock")
    runner = AgentTurnRunner(
        gt_agent=gt_agent,
        system_prompt="You are a test agent.",
        driver_config=AgentDriverConfig(driver_type=DriverType.NATIVE),
    )

    # mock history
    history = MagicMock()
    history.finalize_history_item = AsyncMock()
    history.append_history_message = AsyncMock()
    runner._history = history

    # mock tool registry
    runner.tool_registry.execute_tool_call = AsyncMock(return_value=ToolExecutionResult(
        tool_call_id="tc-read-image",
        result=_fake_tsp_result(),
        success=True,
    ))
    runner.tool_registry.get_registered_tool = MagicMock(return_value=MagicMock(
        marks_turn_finish=False, self_interrupt=False,
    ))

    # mock activity service
    import service.agentService.agentTurnRunner as m
    m.agentActivityService.add_activity = AsyncMock(return_value=MagicMock(id=7))
    m.agentActivityService.update_activity_progress = AsyncMock()

    room = MagicMock(spec=ChatRoom)
    room.team_id = 1
    room.state = RoomState.IDLE
    output_item = MagicMock(spec=GtAgentHistory)
    output_item.id = 99
    output_item.tool_call_id = "tc-read-image"
    tool_call = llmApiUtil.OpenAIToolCall(
        id="tc-read-image",
        function={"name": "read_image", "arguments": '{"file_path": "联调素材/test_image.png"}'},
    )

    ret = await runner._run_tool_to_item(tool_call, output_item, room)
    assert ret == TurnStepResult.TOOL_EXECUTE_SUCCESS, f"turn result={ret}"

    # 1) turn runner 写单条 TOOL 消息：剥离 base64，图片挂附件，不追加 USER 消息
    history.append_history_message.assert_not_called(), "turn runner 不应自行追加 USER 图片消息"
    tool_msg = history.finalize_history_item.await_args.kwargs["message"]
    assert tool_msg.role == OpenaiApiRole.TOOL
    assert "base64" not in tool_msg.content, "TOOL 消息不应包含 base64"
    assert '"format": "png"' in tool_msg.content, "TOOL 消息应保留元数据"
    assert tool_msg.attachments is not None and tool_msg.attachments[0].data == _fake_tsp_result()["base64"]
    print("1. turn runner 写单条 TOOL 消息（剥 base64 + 图片挂附件）: PASS")

    # 2) llmService 发送时拆分：TOOL 文本 + USER 图片消息
    from service.llmService.core import _split_tool_result_messages
    sent = _split_tool_result_messages([tool_msg])
    assert len(sent) == 2, f"应拆成 TOOL 文本 + USER 图片两条，实际 {len(sent)}"
    assert sent[0].role == OpenaiApiRole.TOOL and isinstance(sent[0].content, str) and "base64" not in sent[0].content
    assert sent[1].role == OpenaiApiRole.USER
    image_blocks = [b for b in sent[1].content if isinstance(b, llmApiUtil.OpenAIImageUrlContentBlock)]
    assert len(image_blocks) == 1
    url = image_blocks[0].image_url["url"]
    assert url.startswith("data:image/png;base64,"), f"data URL 格式错误: {url[:40]}"
    assert url.endswith(_fake_tsp_result()["base64"]), "data URL 应包含完整 base64"
    print("2. llmService 拆分 TOOL 文本 + USER 图片: PASS")
    print("3. data URL 组装: PASS ->", url[:60] + "...")

    print("\nHOST SIDE VERIFY: PASS")


if __name__ == "__main__":
    asyncio.run(main())
