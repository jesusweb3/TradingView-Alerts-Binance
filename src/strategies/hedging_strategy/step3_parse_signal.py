# src/strategies/hedging_strategy/step3_parse_signal.py

from typing import Dict, Any
from src.utils.logger import get_logger

logger = get_logger(__name__)


class ParseSignal:
    """Парсинг сообщения от TradingView в торговый сигнал"""

    @staticmethod
    def execute(message: str) -> Dict[str, Any]:
        """
        Парсит сообщение от webhook в торговый сигнал

        Args:
            message: Сырое текстовое сообщение от TradingView

        Returns:
            {
                'action': 'buy' | 'sell' | None,
                'parsed': bool,
                'raw_message': str
            }
        """
        try:
            if not message or not isinstance(message, str):
                logger.warning("Пустое или некорректное сообщение")
                return {
                    'action': None,
                    'parsed': False,
                    'raw_message': str(message) if message else ''
                }

            message_lower = message.strip().lower()

            if 'buy' in message_lower:
                logger.info(f"Парсинг: найден сигнал BUY из сообщения '{message}'")
                return {
                    'action': 'buy',
                    'parsed': True,
                    'raw_message': message
                }
            elif 'sell' in message_lower:
                logger.info(f"Парсинг: найден сигнал SELL из сообщения '{message}'")
                return {
                    'action': 'sell',
                    'parsed': True,
                    'raw_message': message
                }
            else:
                logger.warning(f"В сообщении '{message}' не найдено действие buy/sell")
                return {
                    'action': None,
                    'parsed': False,
                    'raw_message': message
                }

        except Exception as e:
            logger.error(f"Ошибка парсинга сигнала: {e}")
            raise