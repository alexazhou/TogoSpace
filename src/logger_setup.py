import logging
import os
from datetime import datetime


def setup_logger(module_name: str, log_dir: str = None) -> logging.Logger:
    """
    设置日志系统，输出到控制台和文件

    Args:
        module_name: 模块名称，用于创建 logger
        log_dir: 日志目录路径，如果为 None 则使用项目根目录下的 logs 文件夹

    Returns:
        配置好的 logger 实例
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

    # 创建并返回指定模块的 logger
    logger = logging.getLogger(module_name)
    logger.info(f"日志文件: {log_file}")

    return logger
