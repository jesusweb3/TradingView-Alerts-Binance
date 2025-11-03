# src/strategies/hedging_strategy/step14_start_dual_tracking.py

from typing import Dict, Any, Callable, Coroutine
from src.utils.logger import get_logger

logger = get_logger(__name__)


class StartDualTracking:
    """Запуск параллельного отслеживания SL и TRIGGER цен"""

    def __init__(self, price_stream):
        self.price_stream = price_stream

    async def execute(
        self,
        sl_price: float,
        trigger_price: float,
        hedge_position_side: str,
        on_sl_callback: Callable[[float], Coroutine],
        on_trigger_callback: Callable[[float], Coroutine]
    ) -> Dict[str, Any]:
        """
        Запускает два параллельных watch_price для SL и TRIGGER

        Направления (ждём какой первый срабатит):
        SHORT хедж:
          - SL (выше entry) → direction='long' (цена должна расти вверх)
          - TRIGGER (ниже entry) → direction='short' (цена должна падать вниз)

        LONG хедж:
          - SL (ниже entry) → direction='short' (цена должна падать вниз)
          - TRIGGER (выше entry) → direction='long' (цена должна расти вверх)

        Args:
            sl_price: Цена SL
            trigger_price: Цена триггера
            hedge_position_side: 'LONG' или 'SHORT'
            on_sl_callback: Async callback при срабатывании SL
            on_trigger_callback: Async callback при срабатывании TRIGGER

        Returns:
            {
                'success': bool,
                'sl_price': float,
                'trigger_price': float,
                'sl_direction': str,
                'trigger_direction': str,
                'sl_watch_key': str,
                'trigger_watch_key': str,
                'error': str | None
            }
        """
        try:
            if hedge_position_side == 'LONG':
                sl_direction = 'short'           # SL ниже entry, цена падает
                trigger_direction = 'long'       # TRIGGER выше entry, цена растет
            else:  # SHORT
                sl_direction = 'long'            # SL выше entry, цена растет
                trigger_direction = 'short'      # TRIGGER ниже entry, цена падает

            logger.info(
                f"Запускаем двойное отслеживание для {hedge_position_side} хеджа:"
            )
            logger.info(
                f"  SL: ${sl_price:.2f} ({sl_direction})"
            )
            logger.info(
                f"  TRIGGER: ${trigger_price:.2f} ({trigger_direction})"
            )
            logger.info(
                f"Оба watch'а активны параллельно - ждём какой первый срабатит"
            )

            self.price_stream.watch_price(
                target_price=sl_price,
                direction=sl_direction,
                on_reach=on_sl_callback,
                barrier_price=None,
                barrier_side=None
            )

            logger.info(f"Watch на SL запущен: ${sl_price:.2f} ({sl_direction})")

            self.price_stream.watch_price(
                target_price=trigger_price,
                direction=trigger_direction,
                on_reach=on_trigger_callback,
                barrier_price=None,
                barrier_side=None
            )

            logger.info(f"Watch на TRIGGER запущен: ${trigger_price:.2f} ({trigger_direction})")

            sl_watch_key = f"{sl_price}_{sl_direction}_None_None"
            trigger_watch_key = f"{trigger_price}_{trigger_direction}_None_None"

            logger.info(
                "Двойное отслеживание активно. "
                "Ждём срабатывания SL или TRIGGER (в зависимости от движения цены)"
            )

            return {
                'success': True,
                'sl_price': sl_price,
                'trigger_price': trigger_price,
                'sl_direction': sl_direction,
                'trigger_direction': trigger_direction,
                'sl_watch_key': sl_watch_key,
                'trigger_watch_key': trigger_watch_key,
                'error': None
            }

        except Exception as e:
            logger.error(f"Ошибка при запуске двойного отслеживания: {e}")
            return {
                'success': False,
                'sl_price': sl_price,
                'trigger_price': trigger_price,
                'sl_direction': None,
                'trigger_direction': None,
                'sl_watch_key': None,
                'trigger_watch_key': None,
                'error': str(e)
            }