import asyncio
import logging
import os
from datetime import datetime

import util.llm_api_util as llm_api_util
from util.config_util import load_config, load_llm_service_config
from service import message_bus, scheduler_service as scheduler, agent_service, room_service as chat_room, llm_service, func_tool_service as agent_tools


def _setup_logger() -> None:
    log_dir = os.path.join(os.getcwd(), "../logs")
    os.makedirs(log_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    log_file = os.path.join(log_dir, f"v3_chat_{timestamp}.log")

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

    rooms_config = config["chat_rooms"]
    for r in rooms_config:
        chat_room.init(name=r["name"], initial_topic=r["initial_topic"])

    message_bus.init()
    llm_cfg = load_llm_service_config()
    llm_api_util.init()
    llm_service.init(api_key=llm_cfg["api_key"], base_url=llm_cfg["base_url"])
    agent_tools.init()
    agent_service.init(config["agents"], rooms_config)
    scheduler.init(
        rooms_config=rooms_config,
        max_function_calls=config.get("max_function_calls", 5),
    )

    # 为每个房间添加初始话题
    for r in rooms_config:
        initial_topic = chat_room.get_room(r["name"]).initial_topic
        if initial_topic:
            chat_room.get_room(r["name"]).add_message("system", initial_topic)

    try:
        await scheduler.run()
    finally:
        scheduler.stop()
        agent_service.close()
        agent_tools.close()
        chat_room.close_all()
        message_bus.stop()


if __name__ == "__main__":
    asyncio.run(main())
