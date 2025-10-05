# src/utils/logger.py

import logging
import sys
import os
from datetime import datetime


class MillisecondFormatter(logging.Formatter):
    """Форматтер с миллисекундами (3 цифры)"""

    def formatTime(self, record, datefmt=None):
        if datefmt:
            s = datetime.fromtimestamp(record.created).strftime(datefmt)
            s = f"{s}.{int(record.msecs):03d}"
        else:
            s = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
            s = f"{s}.{int(record.msecs):03d}"
        return s


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """
    Создает логгер с консольным и файловым выводом

    Args:
        name: Имя логгера
        level: Уровень логирования

    Returns:
        Настроенный логгер
    """
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    logger.setLevel(level)

    formatter = MillisecondFormatter(
        fmt='%(asctime)s | %(levelname)-8s | %(name)-30s | %(message)s',
        datefmt='%d-%m-%y %H:%M:%S'
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)

    logs_dir = "logs"
    if not os.path.exists(logs_dir):
        os.makedirs(logs_dir)

    current_date = datetime.now().strftime('%Y-%m-%d')
    log_file_path = os.path.join(logs_dir, f"{current_date}.log")

    file_handler = logging.FileHandler(log_file_path, mode='a', encoding='utf-8')
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    logger.propagate = False

    return logger


def set_log_level(level: int) -> None:
    """
    Устанавливает уровень логирования для всех логгеров

    Args:
        level: Уровень логирования
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    for handler in root_logger.handlers:
        handler.setLevel(level)