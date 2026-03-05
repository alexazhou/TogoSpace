import asyncio
import logging
import os

from util.config_util import setup_logger, load_config, load_api_key
import service.scheduler_service as scheduler
import service.agent_service as agent_service
import service.chat_room_service as chat_room
import service.llm_api_service as api_client
import service.agent_tool_service as agent_tools


async def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    setup_logger()
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
