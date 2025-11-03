# src/strategies/hedging_strategy/step10_start_activation_tracking.py

from typing import Dict, Any, Optional, Callable, Coroutine
from src.utils.logger import get_logger

logger = get_logger(__name__)


class StartActivationTracking:
    """Запуск отслеживания цены активации для открытия хеджа"""

    def __init__(self, exchange, price_stream):
        self.exchange = exchange
        self.price_stream = price_stream
        self.symbol = exchange.symbol

    async def execute(
        self,
        main_position_side: str,
        main_entry_price: float,
        leverage: int,
        activation_pnl: float,
        on_activation_callback: Callable[[float], Coroutine],
        barrier_price: Optional[float] = None,
        barrier_side: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Запускает отслеживание цены активации хеджа

        Формулы:
        LONG:  activation_price = entry_price + (pnl / leverage)
        SHORT: activation_price = entry_price - (pnl / leverage)

        Args:
            main_position_side: 'LONG' или 'SHORT'
            main_entry_price: ТВХ основной позиции
            leverage: Кредитное плечо (из конфига)
            activation_pnl: PNL процент активации (обычно -5)
            on_activation_callback: Async callback при срабатывании
            barrier_price: Опциональный уровень для barrier-логики (перезапуск после TP)
            barrier_side: 'above' или 'below' (направление пересечения барьера)

        Returns:
            {
                'success': bool,
                'activation_price': float,
                'direction': str,  # 'long' или 'short'
                'barrier_price': float | None,
                'barrier_side': str | None,
                'watch_key': str,
                'error': str | None
            }
        """
        try:
            movement = activation_pnl / leverage

            if main_position_side == 'LONG':
                activation_price = main_entry_price + movement
                direction = 'short'  # Цена должна падать для LONG
            else:  # SHORT
                activation_price = main_entry_price - movement
                direction = 'long'   # Цена должна расти для SHORT

            activation_price = self.exchange.round_price(self.symbol, activation_price)

            logger.info(
                f"Запускаем отслеживание активации хеджа: "
                f"основная {main_position_side} (ТВХ=${main_entry_price:.2f}), "
                f"цель активации ${activation_price:.2f} (PNL {activation_pnl}%), "
                f"направление {direction}"
            )

            if barrier_price is not None and barrier_side is not None:
                logger.info(
                    f"С barrier-логикой: цена должна пересечь ${barrier_price:.2f} ({barrier_side})"
                )

            self.price_stream.watch_price(
                target_price=activation_price,
                direction=direction,
                on_reach=on_activation_callback,
                barrier_price=barrier_price,
                barrier_side=barrier_side
            )

            watch_key = f"{activation_price}_{direction}_{barrier_price}_{barrier_side}"

            logger.info(f"Отслеживание активации запущено (watch_key={watch_key})")

            return {
                'success': True,
                'activation_price': activation_price,
                'direction': direction,
                'barrier_price': barrier_price,
                'barrier_side': barrier_side,
                'watch_key': watch_key,
                'error': None
            }

        except Exception as e:
            logger.error(f"Ошибка запуска отслеживания активации: {e}")
            return {
                'success': False,
                'activation_price': None,
                'direction': None,
                'barrier_price': barrier_price,
                'barrier_side': barrier_side,
                'watch_key': None,
                'error': str(e)
            }