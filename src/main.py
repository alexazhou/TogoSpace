import asyncio
import json
import logging
import os
from datetime import datetime

from core.agent import Agent
from core.chat_room import ChatRoom
from core.scheduler import Scheduler


def setup_logger() -> None:
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    log_dir = os.path.join(project_root, "logs")
    os.makedirs(log_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    log_file = os.path.join(log_dir, f"v2_chat_{timestamp}.log")

    log_format = "%(asctime)s.%(msecs)03d - %(name)s - %(levelname)s - %(message)s"
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()

    for handler in [
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler()
    ]:
        handler.setFormatter(logging.Formatter(log_format, datefmt="%Y-%m-%d %H:%M:%S"))
        root_logger.addHandler(handler)


def load_config() -> dict:
    config_path = os.path.join(os.path.dirname(__file__), "../config/agents_v2.json")
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_prompt(file_path: str) -> str:
    full_path = os.path.join(os.path.dirname(__file__), "../", file_path)
    with open(full_path, "r", encoding="utf-8") as f:
        return f.read().strip()


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

    scheduler = Scheduler(
        agents=agents,
        chat_room=chat_room,
        max_turns=config.get("max_turns", 6)
    )
    await scheduler.run()


if __name__ == "__main__":
    asyncio.run(main())
