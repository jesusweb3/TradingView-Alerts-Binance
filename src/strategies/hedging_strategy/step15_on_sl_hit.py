# src/strategies/hedging_strategy/step15_on_sl_hit.py

from typing import Dict, Any
from src.utils.logger import get_logger

logger = get_logger(__name__)


class OnSLHit:
    """Обработка срабатывания SL - хедж закрывается в убыток"""

    def __init__(self, price_stream):
        self.price_stream = price_stream

    async def execute(
        self,
        current_price: float,
        sl_price: float,
        failure_count: int,
        max_failures: int
    ) -> Dict[str, Any]:
        """
        Обрабатывает срабатывание SL (хедж закрыт в убыток)

        Процесс:
        1. Отменить оба watch'а (SL + TRIGGER)
        2. failure_count++
        3. Если failure_count >= max_failures → цикл стоп
        4. Иначе → перезагрузка (вернуться к step10)

        Args:
            current_price: Текущая цена при срабатывании
            sl_price: Цена SL которую достигли
            failure_count: Текущий счётчик неудачных хеджей
            max_failures: Максимально допустимое количество неудачных хеджей

        Returns:
            {
                'success': bool,
                'sl_hit_price': float,
                'failure_count': int,  # Новое значение
                'max_failures': int,
                'cycle_stopped': bool,  # True если лимит достигнут
                'should_restart': bool,  # True если перезагружать от step10
                'new_hedge_entry_price': None,  # Обнуляем
                'new_active_stop_order_id': None,  # Обнуляем
                'error': str | None
            }
        """
        try:
            logger.warning(
                f"SL хеджа СРАБАТИЛ! Цена достигла ${current_price:.2f} (SL был ${sl_price:.2f})"
            )

            logger.info("Отменяем оба watch'а (SL + TRIGGER)")

            try:
                self.price_stream.cancel_all_watches()
                logger.info("Все watch'и отменены")
            except Exception as e:
                logger.error(f"Ошибка при отмене watch'ей: {e}")

            new_failure_count = failure_count + 1

            logger.warning(
                f"Убыточных хеджей: {new_failure_count}/{max_failures}"
            )

            if new_failure_count >= max_failures:
                logger.error(
                    f"Достигнут лимит убыточных хеджей ({new_failure_count}). "
                    f"Цикл хеджирования ОСТАНОВЛЕН. Ждём нового сигнала с только основной позицией."
                )

                return {
                    'success': True,
                    'sl_hit_price': current_price,
                    'failure_count': new_failure_count,
                    'max_failures': max_failures,
                    'cycle_stopped': True,
                    'should_restart': False,
                    'new_hedge_entry_price': None,
                    'new_active_stop_order_id': None,
                    'error': None
                }

            else:
                logger.info(
                    f"Неудачных хеджей: {new_failure_count}/{max_failures}. "
                    f"Можем открыть новый хедж. Перезагружаем цикл отслеживания активации."
                )

                return {
                    'success': True,
                    'sl_hit_price': current_price,
                    'failure_count': new_failure_count,
                    'max_failures': max_failures,
                    'cycle_stopped': False,
                    'should_restart': True,
                    'new_hedge_entry_price': None,
                    'new_active_stop_order_id': None,
                    'error': None
                }

        except Exception as e:
            logger.error(f"Ошибка обработки SL срабатывания: {e}")
            return {
                'success': False,
                'sl_hit_price': current_price,
                'failure_count': failure_count,
                'max_failures': max_failures,
                'cycle_stopped': False,
                'should_restart': False,
                'new_hedge_entry_price': None,
                'new_active_stop_order_id': None,
                'error': str(e)
            }