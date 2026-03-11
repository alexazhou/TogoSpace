import argparse
import asyncio
import logging
import os
import signal
import sys
from datetime import datetime

import tornado.httpserver
import util.llm_api_util as llm_api_util
from util.config_util import load_config, load_llm_service_config
from service import message_bus, scheduler_service as scheduler, agent_service, room_service as chat_room, llm_service, func_tool_service
from controller.ws_controller import init as init_ws
from route import make_app


def _setup_logger() -> None:
    log_dir = os.path.join(os.getcwd(), "../logs/backend")
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


_RUN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../run")
_PID_FILE = os.path.join(_RUN_DIR, "backend.pid")


def _check_single_instance() -> None:
    os.makedirs(_RUN_DIR, exist_ok=True)
    # 读取已有 PID，检查进程是否存活
    try:
        with open(_PID_FILE) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)  # 进程存活则抛 OSError
        print(f"后端已在运行（PID {pid}），拒绝启动第二个实例。", file=sys.stderr)
        sys.exit(1)
    except (FileNotFoundError, ValueError, ProcessLookupError):
        pass  # 文件不存在、内容非法、进程不存在，均视为可启动


def _write_pid() -> None:
    os.makedirs(_RUN_DIR, exist_ok=True)
    with open(_PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def _remove_pid() -> None:
    try:
        os.remove(_PID_FILE)
    except FileNotFoundError:
        pass


async def main(config_path: str = None, llm_config_path: str = None, port: int = 8080):
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    _setup_logger()
    logger = logging.getLogger(__name__)

    config = load_config(config_path)

    from constants import RoomType
    rooms_config = config["chat_rooms"]
    for r in rooms_config:
        room_type = RoomType(r.get("type", "group"))
        chat_room.init(name=r["name"], initial_topic=r["initial_topic"], room_type=room_type)

    message_bus.init()
    llm_cfg = load_llm_service_config(llm_config_path)
    llm_api_util.init()
    llm_service.init(api_key=llm_cfg["api_key"], base_url=llm_cfg["base_url"])
    func_tool_service.init()
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

    init_ws()
    web_server = tornado.httpserver.HTTPServer(make_app())
    web_server.listen(port, "0.0.0.0")
    logger.info(f"Web API 服务已启动: http://0.0.0.0:{port}")

    try:
        await asyncio.gather(
            scheduler.run(),
            asyncio.Event().wait(),
        )
    finally:
        web_server.stop()
        scheduler.stop()
        agent_service.close()
        func_tool_service.close()
        chat_room.close_all()
        message_bus.stop()
        _remove_pid()


if __name__ == "__main__":
    _check_single_instance()
    _write_pid()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=None, help="agents 配置文件路径")
    parser.add_argument("--llm-config", default=None, dest="llm_config", help="LLM 服务配置文件路径")
    parser.add_argument("--port", type=int, default=8080, help="HTTP 监听端口")
    args = parser.parse_args()
    asyncio.run(main(config_path=args.config, llm_config_path=args.llm_config, port=args.port))
