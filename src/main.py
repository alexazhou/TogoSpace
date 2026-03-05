import asyncio
import logging
import os
from datetime import datetime

from util.config_util import load_config, load_api_key
import service.scheduler_service as scheduler
import service.agent_service as agent_service
import service.chat_room_service as chat_room
import service.llm_api_service as api_client
import service.agent_tool_service as agent_tools


def _setup_logger() -> None:
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../logs")
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


async def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    _setup_logger()
    logger = logging.getLogger(__name__)

    config = load_config()

    room_name = config["chat_room"]["name"]
    chat_room.init(name=room_name, initial_topic=config["chat_room"]["initial_topic"])
    agent_service.init(config["agents"])
    api_client.init(load_api_key())
    agent_tools.init()
    scheduler.init(
        room_name=room_name,
        max_turns=config.get("max_turns", 6),
        max_function_calls=config.get("max_function_calls", 5),
    )

    # 添加初始话题
    initial_topic = chat_room.get_room(room_name).initial_topic
    if initial_topic:
        chat_room.add_message(room_name, "system", initial_topic)

    try:
        await scheduler.run()
    finally:
        scheduler.stop()
        agent_service.close()
        agent_tools.close()
        chat_room.close_all()
        await api_client.close()


if __name__ == "__main__":
    asyncio.run(main())
