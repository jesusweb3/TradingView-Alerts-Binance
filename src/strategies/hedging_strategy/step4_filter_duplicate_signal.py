# src/strategies/hedging_strategy/step4_filter_duplicate_signal.py

from typing import Optional, Dict, Any
from src.utils.logger import get_logger

logger = get_logger(__name__)


class FilterDuplicateSignal:
    """Фильтр дубликатов торговых сигналов"""

    @staticmethod
    def execute(action: Optional[str], last_action: Optional[str]) -> Dict[str, Any]:
        """
        Проверяет, не является ли сигнал дубликатом предыдущего

        Args:
            action: Текущий сигнал ('buy' или 'sell' или None)
            last_action: Последний обработанный сигнал

        Returns:
            {
                'should_process': bool,
                'is_duplicate': bool,
                'action': str | None,
                'last_action_before': str | None,
                'new_last_action': str | None
            }
        """
        try:
            if action is None:
                logger.warning("Сигнал не распарсен - пропускаем")
                return {
                    'should_process': False,
                    'is_duplicate': False,
                    'action': None,
                    'last_action_before': last_action,
                    'new_last_action': last_action
                }

            if last_action is None:
                logger.info(f"Первый сигнал {action.upper()} - обрабатываем")
                return {
                    'should_process': True,
                    'is_duplicate': False,
                    'action': action,
                    'last_action_before': None,
                    'new_last_action': action
                }

            if action == last_action:
                logger.info(f"Дублирующий сигнал {action.upper()} (был {last_action.upper()}) - игнорируем")
                return {
                    'should_process': False,
                    'is_duplicate': True,
                    'action': action,
                    'last_action_before': last_action,
                    'new_last_action': last_action
                }

            logger.info(f"Новый сигнал {action.upper()} (был {last_action.upper()}) - обрабатываем")
            return {
                'should_process': True,
                'is_duplicate': False,
                'action': action,
                'last_action_before': last_action,
                'new_last_action': action
            }

        except Exception as e:
            logger.error(f"Ошибка фильтрации дубликатов: {e}")
            raise