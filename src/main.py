import asyncio
import logging
import os

from util.config_util import setup_logger, load_config, load_prompt, load_api_key
from service.agent_service import Agent
from service.chat_room_service import ChatRoom
from service.scheduler_service import Scheduler
from service.api_client_service import APIClient


async def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    setup_logger()
    logger = logging.getLogger(__name__)

    config = load_config()

    # 创建聊天室
    chat_room = ChatRoom(
        name=config["chat_room"]["name"],
        initial_topic=config["chat_room"]["initial_topic"]
    )

    # 创建 Agent 实例（数量由配置决定）
    agent_names = [a["name"] for a in config["agents"]]
    agents = []
    for agent_config in config["agents"]:
        other_names = [n for n in agent_names if n != agent_config["name"]]
        prompt = load_prompt(agent_config["prompt_file"])
        prompt = prompt.replace("{participants}", "、".join(other_names))
        agents.append(Agent(
            name=agent_config["name"],
            system_prompt=prompt,
            model=agent_config["model"]
        ))

    logger.info(f"已创建 {len(agents)} 个 Agent: {agent_names}")

    # 添加初始话题
    if chat_room.initial_topic:
        chat_room.add_message("system", chat_room.initial_topic)

    api_client = APIClient(load_api_key())
    try:
        scheduler = Scheduler(
            agents=agents,
            chat_room=chat_room,
            max_turns=config.get("max_turns", 6),
            api_client=api_client
        )
        await scheduler.run()
    finally:
        await api_client.close()


if __name__ == "__main__":
    asyncio.run(main())
