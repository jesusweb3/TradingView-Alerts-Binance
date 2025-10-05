# src/telegram/handler.py

import logging
from typing import Optional
from src.telegram.notifier import TelegramNotifier


class TelegramLogHandler(logging.Handler):
    """Handler для отправки логов в Telegram"""

    def __init__(self, notifier: TelegramNotifier, level=logging.WARNING):
        super().__init__(level)
        self.notifier = notifier

    def emit(self, record: logging.LogRecord):
        """
        Отправляет лог запись в Telegram

        Args:
            record: Запись лога
        """
        try:
            log_entry = self.format(record)

            if record.levelno >= logging.ERROR:
                self.notifier.send_error(log_entry)
            elif record.levelno >= logging.WARNING:
                self.notifier.send_warning(log_entry)

        except (OSError, RuntimeError):
            self.handleError(record)


_telegram_notifier: Optional[TelegramNotifier] = None


def initialize_telegram(notifier: TelegramNotifier):
    """
    Инициализирует глобальный Telegram notifier и добавляет handler к root logger

    Args:
        notifier: Экземпляр TelegramNotifier
    """
    global _telegram_notifier
    _telegram_notifier = notifier

    root_logger = logging.getLogger()
    telegram_handler = TelegramLogHandler(notifier)

    formatter = logging.Formatter('%(name)s | %(message)s')
    telegram_handler.setFormatter(formatter)

    root_logger.addHandler(telegram_handler)


def get_telegram_notifier() -> Optional[TelegramNotifier]:
    """
    Возвращает глобальный Telegram notifier

    Returns:
        Экземпляр TelegramNotifier или None если не инициализирован
    """
    return _telegram_notifier