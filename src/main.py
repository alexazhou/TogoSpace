import asyncio
import json
import logging
import os
import sys
from datetime import datetime

import aiohttp
import certifi
import ssl
import traceback

from api.client import APIClient
from core.agent import Agent
from core.chat_room import ChatRoom
from function_loader import build_tools, execute_function
from functions import set_chat_context


def setup_logger(log_dir: str = None) -> None:
    """
    设置日志系统，输出到控制台和文件

    Args:
        log_dir: 日志目录路径，如果为 None 则使用项目根目录下的 logs 文件夹
    """
    # 确定日志目录
    if log_dir is None:
        # 获取项目根目录（src 目录的上一级）
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        log_dir = os.path.join(project_root, "logs")

    # 确保日志目录存在
    os.makedirs(log_dir, exist_ok=True)

    # 创建带时间戳的日志文件
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    log_file = os.path.join(log_dir, f"v1_chat_{timestamp}.log")

    # 日志格式（包含模块名）
    log_format = "%(asctime)s.%(msecs)03d - %(name)s - %(levelname)s - %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    # 配置 root logger，这样所有子 logger 都会继承
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # 清除现有的 handlers
    root_logger.handlers.clear()

    # 文件处理器
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter(log_format, datefmt=date_format))

    # 控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(log_format, datefmt=date_format))

    # 添加 handlers 到 root logger
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)


def load_config() -> dict:
    """加载配置"""
    config_path = os.path.join(os.path.dirname(__file__), "../config/agents_v1.json")
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_api_key() -> str:
    """加载 API Key"""
    config_path = os.path.join(os.path.dirname(__file__), "../config.json")
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
        return config["anthropic"]["api_key"]


def load_prompt(file_path: str) -> str:
    """加载提示词"""
    full_path = os.path.join(os.path.dirname(__file__), "../", file_path)
    with open(full_path, "r", encoding="utf-8") as f:
        return f.read().strip()


async def main():
    # 切换到脚本所在目录
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)

    # 设置日志
    setup_logger()
    logger = logging.getLogger(__name__)

    # 加载配置
    config = load_config()
    api_key = load_api_key()

    # 创建聊天室
    chat_room = ChatRoom(
        name=config["chat_room"]["name"],
        initial_topic=config["chat_room"]["initial_topic"]
    )

    # 创建 API 客户端 session
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    connector = aiohttp.TCPConnector(ssl=ssl_context)

    async with aiohttp.ClientSession(connector=connector) as session:
        # 创建 API 客户端
        api_client = APIClient(api_key=api_key, session=session)

        # 创建 Agents
        agents = []
        for agent_config in config["agents"]:
            prompt = load_prompt(agent_config["prompt_file"])
            agent = Agent(
                name=agent_config["name"],
                system_prompt=prompt,
                model=agent_config["model"]
            )
            agents.append(agent)

        logger.info(f"已创建 {len(agents)} 个 Agent: {[a.name for a in agents]}")

        # 构建 tools 列表
        tools = build_tools()
        logger.info(f"已加载 {len(tools)} 个工具")

        # 添加初始话题
        if chat_room.initial_topic:
            chat_room.add_message("system", chat_room.initial_topic)
            logger.info(f"初始话题: {chat_room.initial_topic}")

        # 轮流对话
        max_turns = config.get("max_turns", 5)
        logger.info(f"开始 {max_turns} 轮对话...")

        for turn in range(1, max_turns + 1):
            logger.info(f"\n--- 第 {turn} 轮 ---")

            # 使用模运算选择当前轮次应该说话的 agent
            current_agent = agents[(turn - 1) % len(agents)]

            # 获取上下文
            context_messages = chat_room.get_context_messages()
            logger.info(f"[{current_agent.name}] 上下文消息数: {len(context_messages)}")

            # 设置函数调用上下文
            set_chat_context(chat_room, current_agent.name)

            # 生成回复
            try:
                final_response, tool_calls_info = await current_agent.generate_with_function_calling(
                    api_client=api_client,
                    context_messages=context_messages,
                    tools=tools,
                    function_executor=execute_function,
                    max_function_calls=5
                )

                # 记录工具调用信息
                if tool_calls_info:
                    logger.info(f"[{current_agent.name}] 工具调用信息: {tool_calls_info}")

                logger.info(f"{current_agent.name}: {final_response}")
            except Exception as e:
                logger.error(f"{current_agent.name} 生成回复失败: {e}")
                traceback.print_exc()
                return

        # 输出完整聊天记录
        logger.info(f"\n{chat_room.format_log()}")


if __name__ == "__main__":
    asyncio.run(main())
