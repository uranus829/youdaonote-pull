import logging
import os
import sys
from datetime import datetime

from core.common import get_script_directory

LOG_FORMAT = "%(asctime)s %(levelname)s %(processName)s-%(threadName)s-%(thread)d %(filename)s:%(lineno)d %(funcName)-10s : %(message)s"
DATE_FORMAT = "%Y/%m/%d %H:%M:%S "


def init_logging():
    """
    初始化日志系统：
    1. 避免重复添加 handler 导致日志重复打印
    2. 兼容 Windows 控制台 UTF-8 特殊符号打印，防止 UnicodeEncodeError 报错崩溃
    """
    root_logger = logging.getLogger()
    if root_logger.hasHandlers():
        return  # 避免多次调用导致日志重复输出多遍

    log_dir = os.path.join(get_script_directory(), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_filename = os.path.join(
        log_dir, f"pull-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
    )

    # 1. 写入日志文件（固定使用 utf-8 编码）
    file_handler = logging.FileHandler(log_filename, "a", encoding="utf-8")

    # 2. 控制台输出：在 Windows 下保护 stdout，避免笔记里的特殊符号/表情导致 GBK 编码报错崩溃
    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    console_handler = logging.StreamHandler(sys.stdout)

    logging.basicConfig(
        handlers=[file_handler, console_handler],
        level=logging.INFO,
        format=LOG_FORMAT,
        datefmt=DATE_FORMAT,
    )
