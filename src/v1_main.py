import asyncio
import json
import os
import sys

# 添加父目录到 path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.agent import Agent
from core.chat_room import ChatRoom
from api.client import APIClient
from logger_setup import setup_logger

# 设置日志
logger = setup_logger(__name__)


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
    # 加载配置
    config = load_config()
    api_key = load_api_key()

    # 创建聊天室
    chat_room = ChatRoom(
        name=config["chat_room"]["name"],
        initial_topic=config["chat_room"]["initial_topic"]
    )

    # 创建 API 客户端 session
    import aiohttp
    import ssl
    import certifi

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

        # 添加初始话题
        if chat_room.initial_topic:
            chat_room.add_message("system", chat_room.initial_topic)
            logger.info(f"初始话题: {chat_room.initial_topic}")

        # 轮流对话
        max_turns = config.get("max_turns", 5)
        logger.info(f"开始 {max_turns} 轮对话...")

        for turn in range(1, max_turns + 1):
            logger.info(f"\n--- 第 {turn} 轮 ---")

            for agent in agents:
                # 获取上下文
                context = chat_room.get_context()
                logger.info(f"[{agent.name}] 上下文长度: {len(context)}")

                # 生成回复
                try:
                    response = await agent.generate_response(
                        api_client=api_client,
                        context=context
                    )

                    # 添加消息
                    chat_room.add_message(agent.name, response)

                    logger.info(f"{agent.name}: {response}")
                except Exception as e:
                    logger.error(f"{agent.name} 生成回复失败: {e}")
                    import traceback
                    traceback.print_exc()
                    return

        # 输出完整聊天记录
        logger.info(f"\n{chat_room.format_log()}")


if __name__ == "__main__":
    asyncio.run(main())
