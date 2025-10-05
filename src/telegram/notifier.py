# src/telegram/notifier.py

from typing import List
import requests
from src.utils.logger import get_logger

logger = get_logger(__name__)


class TelegramNotifier:
    """Отправка уведомлений в Telegram"""

    def __init__(self, bot_token: str, chat_ids: List[str]):
        self.bot_token = bot_token
        self.chat_ids = chat_ids
        self.base_url = f"https://api.telegram.org/bot{bot_token}"

    def send_message(self, text: str) -> bool:
        """
        Отправляет сообщение всем chat_ids

        Args:
            text: Текст сообщения

        Returns:
            True если хотя бы одно сообщение отправлено успешно
        """
        success_count = 0

        for chat_id in self.chat_ids:
            if self._send_to_chat(chat_id, text):
                success_count += 1

        return success_count > 0

    def _send_to_chat(self, chat_id: str, text: str) -> bool:
        """
        Отправляет сообщение в конкретный чат

        Args:
            chat_id: ID чата
            text: Текст сообщения

        Returns:
            True если сообщение отправлено успешно
        """
        url = f"{self.base_url}/sendMessage"

        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML"
        }

        try:
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()

            result = response.json()

            if result.get("ok"):
                logger.info(f"Telegram уведомление отправлено в чат {chat_id}")
                return True
            else:
                logger.error(f"Ошибка отправки в чат {chat_id}: {result}")
                return False

        except requests.exceptions.RequestException as e:
            logger.error(f"Сетевая ошибка при отправке в чат {chat_id}: {e}")
            return False
        except Exception as e:
            logger.error(f"Неожиданная ошибка при отправке в чат {chat_id}: {e}")
            return False

    def send_warning(self, message: str) -> bool:
        """
        Отправляет предупреждение

        Args:
            message: Текст предупреждения

        Returns:
            True если отправлено успешно
        """
        text = f"⚠️ <b>ПРЕДУПРЕЖДЕНИЕ</b>\n\n{message}"
        return self.send_message(text)

    def send_error(self, message: str) -> bool:
        """
        Отправляет сообщение об ошибке

        Args:
            message: Текст ошибки

        Returns:
            True если отправлено успешно
        """
        text = f"🚨 <b>ОШИБКА</b>\n\n{message}"
        return self.send_message(text)

    def send_critical(self, message: str) -> bool:
        """
        Отправляет критическое сообщение

        Args:
            message: Текст критической ошибки

        Returns:
            True если отправлено успешно
        """
        text = f"🆘 <b>КРИТИЧЕСКАЯ ОШИБКА</b>\n\n{message}"
        return self.send_message(text)

    def test_connection(self) -> bool:
        """
        Тестирует подключение к Telegram API

        Returns:
            True если подключение успешно
        """
        url = f"{self.base_url}/getMe"

        try:
            response = requests.get(url, timeout=5)
            response.raise_for_status()

            result = response.json()

            if result.get("ok"):
                bot_info = result.get("result", {})
                logger.info(f"Telegram бот подключен: {bot_info.get('first_name', 'Unknown')}")
                return True
            else:
                logger.error(f"Ошибка подключения к Telegram боту: {result}")
                return False

        except Exception as e:
            logger.error(f"Ошибка тестирования Telegram подключения: {e}")
            return False