# src/strategies/hedging_strategy/step5_handle_incoming_signal.py

from typing import Dict, Any
from src.utils.logger import get_logger

logger = get_logger(__name__)


class HandleIncomingSignal:
    """Распределение логики по различным сценариям"""

    @staticmethod
    def execute(
            action: str,
            main_position_open: bool,
            hedge_position_open: bool
    ) -> Dict[str, Any]:
        """
        Определяет какой сценарий обработки нужно применить

        Четыре сценария (по состоянию позиций):

        Сценарий Б: Позиций нет (main=0, hedge=0)
          → Открыть новую основную позицию

        Сценарий В: Только основная открыта (main=1, hedge=0)
          → Закрыть текущую основную и открыть новую в противоположном направлении

        Сценарий Г: Обе позиции открыты (main=1, hedge=1)
          → Конвертировать хедж в основную позицию, закрыть старую основную

        Сценарий А: Основная закрыта после старта (main=0, hedge=0)
          → Пропустить закрытие (уже закрыта), затем перейти к Б

        Args:
            action: 'buy' или 'sell'
            main_position_open: True если основная позиция открыта
            hedge_position_open: True если хедж позиция открыта

        Returns:
            {
                'scenario': str,  # Тип сценария
                'next_step': str, # Следующий шаг
                'action': str,
                'details': Dict
            }
        """
        try:
            if not main_position_open and not hedge_position_open:
                logger.info("Сценарий Б: Позиций нет - открываем новую основную")
                return {
                    'scenario': 'no_position',
                    'next_step': 'step7_open_new_main_position',
                    'action': action,
                    'details': {}
                }

            if main_position_open and not hedge_position_open:
                logger.info("Сценарий В: Основная открыта, хеджа нет - разворот")
                return {
                    'scenario': 'main_only',
                    'next_step': 'step8_close_and_reverse_main',
                    'action': action,
                    'details': {}
                }

            if main_position_open and hedge_position_open:
                logger.info("Сценарий Г: Обе позиции открыты - конвертация хеджа")
                return {
                    'scenario': 'main_and_hedge',
                    'next_step': 'step9_convert_hedge_to_main',
                    'action': action,
                    'details': {}
                }

            logger.error("Неизвестный сценарий")
            return {
                'scenario': 'unknown',
                'next_step': None,
                'action': action,
                'details': {
                    'main_position_open': main_position_open,
                    'hedge_position_open': hedge_position_open
                }
            }

        except Exception as e:
            logger.error(f"Ошибка в handle_incoming_signal: {e}")
            raise