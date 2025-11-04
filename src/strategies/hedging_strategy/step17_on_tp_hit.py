# src/strategies/hedging_strategy/step17_on_tp_hit.py

from typing import Dict, Any
from src.utils.logger import get_logger

logger = get_logger(__name__)


class OnTPHit:
    """Обработка срабатывания TP — хедж закрыт в профит, запуск перезагрузки цикла"""

    def __init__(self, price_stream):
        self.price_stream = price_stream

    async def execute(
        self,
        current_price: float,
        tp_price: float,
        main_position_side: str,
        failure_count: int
    ) -> Dict[str, Any]:
        """
        Обрабатывает срабатывание TP (хедж закрыт в профит)

        Процесс:
        1. Отменить watch на TP
        2. Хедж закрыт в профит ✅
        3. failure_count НЕ увеличиваем
        4. Сохранить tp_price для barrier-логики в следующем цикле
        5. Перейти к step10 с barrier-логикой

        Args:
            current_price: Текущая цена при срабатывании TP
            tp_price: Цена TP которую достигли
            main_position_side: 'LONG' или 'SHORT' (основная позиция)
            failure_count: Текущий счётчик (НЕ изменяем)

        Returns:
            {
                'success': bool,
                'tp_hit_price': float,
                'tp_closed_price': float,
                'failure_count': int,  # Остаётся неизменным
                'hedge_closed_in_profit': True,
                'tp_price_for_barrier': float,  # Для barrier-логики
                'barrier_side': str,  # 'above' или 'below'
                'should_restart_cycle': bool,
                'error': str | None
            }
        """
        try:
            logger.info(
                f"TP СРАБАТИЛ! Хедж закрыт в ПРОФИТ! "
                f"Цена достигла ${current_price:.2f} (TP был ${tp_price:.2f})"
            )

            logger.info("Отменяем watch на TP")

            try:
                self.price_stream.cancel_all_watches()
                logger.info("Watch на TP отменён")
            except Exception as e:
                logger.error(f"Ошибка при отмене watch'а на TP: {e}")

            logger.info("Хедж успешно закрыт в ПРОФИТ ✅")
            logger.info(f"Счётчик убыточных хеджей остаётся: {failure_count}/{failure_count}")

            barrier_side = 'below' if main_position_side == 'LONG' else 'above'

            logger.info(
                f"Сохраняем tp_price=${tp_price:.2f} для barrier-логики "
                f"(barrier_side='{barrier_side}')"
            )

            logger.info(
                f"Перезагружаем цикл отслеживания activation_price "
                f"с barrier-логикой (цена должна сначала пересечь ${tp_price:.2f})"
            )

            return {
                'success': True,
                'tp_hit_price': current_price,
                'tp_closed_price': tp_price,
                'failure_count': failure_count,
                'hedge_closed_in_profit': True,
                'tp_price_for_barrier': tp_price,
                'barrier_side': barrier_side,
                'should_restart_cycle': True,
                'error': None
            }

        except Exception as e:
            logger.error(f"Ошибка обработки TP срабатывания: {e}")
            return {
                'success': False,
                'tp_hit_price': current_price,
                'tp_closed_price': tp_price,
                'failure_count': failure_count,
                'hedge_closed_in_profit': False,
                'tp_price_for_barrier': None,
                'barrier_side': None,
                'should_restart_cycle': False,
                'error': str(e)
            }