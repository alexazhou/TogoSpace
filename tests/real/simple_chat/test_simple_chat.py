"""real tests — 端到端场景测试，使用 Mock LLM 控制行为剧本"""
import asyncio
import json
import os
import sys

import pytest
import service.roomService as roomService
import service.agentService as agentService
import service.funcToolService as funcToolService
import service.schedulerService as scheduler
import service.llmService as llmService
from tests.base import ServiceTestCase
from util import configUtil, llmApiUtil


_CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config")

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


@pytest.mark.forked
class TestRealSimpleChat(ServiceTestCase):
    """简单对话场景：两个 agent 在房间中完成对话后退出"""

    requires_backend = False  # 不需要后端子进程，直接用 in-process service
    requires_mock_llm = True
    use_custom_config = True

    @classmethod
    async def async_setup_class(cls):
        """初始化服务和配置"""

        # 加载 LLM 配置并启动服务
        llm_cfg = configUtil.load_llmService_config(_CONFIG_DIR)
        await llmService.startup(llm_cfg.get("api_key", ""), llm_cfg.get("base_url", ""))

        # 启动服务
        await roomService.startup()
        await funcToolService.startup()
        await agentService.startup()

        # 加载配置
        agents_cfgs = configUtil.load_agents(_CONFIG_DIR)
        team_cfgs = configUtil.load_teams(_CONFIG_DIR)

        agentService.load_agent_config(agents_cfgs)
        await agentService.create_team_agents(team_cfgs)

        # 创建房间
        await roomService.create_room("default", "general", ["alice", "bob"])

        # 启动调度器
        await scheduler.startup(team_cfgs)

    @classmethod
    async def async_teardown_class(cls):
        """清理服务"""
        scheduler.shutdown()
        llmService.shutdown()

    async def test_two_agents_chat_and_exit(self):
        """Alice 和 Bob 各发一条消息后房间自动退出"""
        # 初始化 LLM API 客户端（使用当前事件循环）
        llmApiUtil.init()

        room_key = "general@default"

        # 剧本：Alice 先说话，然后 Bob 回复（max_turns=2）

        # Alice 的第 1 轮：发送 "你好 Bob！"
        self.set_mock_response({
            "tool_calls": [{
                "name": "send_chat_msg",
                "arguments": json.dumps({
                    "room_name": "general",
                    "msg": "你好 Bob！"
                })
            }]
        })

        # Bob 的第 1 轮：回复 "你好 Alice！"
        self.set_mock_response({
            "tool_calls": [{
                "name": "send_chat_msg",
                "arguments": json.dumps({
                    "room_name": "general",
                    "msg": "你好 Alice！"
                })
            }]
        })

        # 启动调度器
        run_task = asyncio.create_task(scheduler.run())
        room = roomService.get_room(room_key)
        room.start_scheduling()

        # 等待对话完成（max_turns=2，每人 1 轮）
        for _ in range(10):
            if room.state.value == "idle":
                break
            await asyncio.sleep(0.5)

        scheduler.shutdown()
        await asyncio.wait_for(run_task, timeout=5.0)

        # 验证消息数量：1 条系统公告 + 2 条 agent 消息
        messages = room.messages
        agent_messages = [m for m in messages if m.sender_name != "system"]

        assert len(agent_messages) == 2, f"期望 2 条 agent 消息，实际 {len(agent_messages)} 条"

        # 验证消息内容
        assert agent_messages[0].sender_name == "alice"
        assert agent_messages[0].content == "你好 Bob！"

        assert agent_messages[1].sender_name == "bob"
        assert agent_messages[1].content == "你好 Alice！"

        # 验证房间状态为 idle
        assert room.state.value == "idle"
