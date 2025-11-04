# src/strategies/hedging_strategy/step12_setup_hedge_stops.py

from typing import Dict, Any
from src.utils.logger import get_logger

logger = get_logger(__name__)


class SetupHedgeStops:
    """Расчёт уровней стоп-лосса и триггера для хеджа"""

    def __init__(self, exchange):
        self.exchange = exchange
        self.symbol = exchange.symbol

    @staticmethod
    def execute(
        hedge_entry_price: float,
        hedge_position_side: str,
        leverage: int,
        sl_pnl: float,
        trigger_pnl: float
    ) -> Dict[str, Any]:
        """
        Рассчитывает уровни SL и TRIGGER для хеджа

        Формулы (правильные, через умножение на процент):
        movement_percent = pnl / (100 * leverage)
        LONG хедж:
          - SL = entry * (1 + movement_percent)      # sl_pnl отрицательный (-3)
          - TRIGGER = entry * (1 + movement_percent)  # trigger_pnl положительный (+5)

        SHORT хедж:
          - SL = entry * (1 - movement_percent)      # sl_pnl отрицательный (-3)
          - TRIGGER = entry * (1 - movement_percent)  # trigger_pnl положительный (+5)

        Примеры (leverage=4):
        SHORT хедж, entry=3950, sl_pnl=-3%, trigger_pnl=5%:
          - sl_movement = -3 / 400 = -0.0075
          - SL = 3950 * (1 - (-0.0075)) = 3950 * 1.0075 = 3979.625$ ✓
          - trigger_movement = 5 / 400 = 0.0125
          - TRIGGER = 3950 * (1 - 0.0125) = 3950 * 0.9875 = 3900.625$ ✓

        LONG хедж, entry=4050, sl_pnl=-3%, trigger_pnl=5%:
          - sl_movement = -3 / 400 = -0.0075
          - SL = 4050 * (1 + (-0.0075)) = 4050 * 0.9925 = 4019.625$ ✓
          - trigger_movement = 5 / 400 = 0.0125
          - TRIGGER = 4050 * (1 + 0.0125) = 4050 * 1.0125 = 4100.625$ ✓

        Args:
            hedge_entry_price: ТВХ хеджа
            hedge_position_side: 'LONG' или 'SHORT'
            leverage: Кредитное плечо
            sl_pnl: SL в процентах PNL (обычно -3)
            trigger_pnl: TRIGGER в процентах PNL (обычно +5)

        Returns:
            {
                'success': bool,
                'hedge_position_side': str,
                'hedge_entry_price': float,
                'sl_price': float,
                'trigger_price': float,
                'sl_movement_percent': float,  # Движение цены в %
                'trigger_movement_percent': float,
                'error': str | None
            }
        """
        try:
            logger.info(
                f"Рассчитываем уровни SL и TRIGGER для хеджа "
                f"{hedge_position_side} (ТВХ=${hedge_entry_price:.2f})"
            )

            sl_movement_percent = sl_pnl / (100 * leverage)
            trigger_movement_percent = trigger_pnl / (100 * leverage)

            if hedge_position_side == 'LONG':
                sl_price = hedge_entry_price * (1 + sl_movement_percent)
                trigger_price = hedge_entry_price * (1 + trigger_movement_percent)
                sl_direction = 'вверх'
                trigger_direction = 'вверх'
            else:  # SHORT
                sl_price = hedge_entry_price * (1 - sl_movement_percent)
                trigger_price = hedge_entry_price * (1 - trigger_movement_percent)
                sl_direction = 'вниз'
                trigger_direction = 'вниз'

            logger.info(
                f"SL уровень (PNL {sl_pnl}%): ${sl_price:.2f} "
                f"({sl_direction}, {sl_movement_percent*100:.3f}%)"
            )
            logger.info(
                f"TRIGGER уровень (PNL {trigger_pnl}%): ${trigger_price:.2f} "
                f"({trigger_direction}, {trigger_movement_percent*100:.3f}%)"
            )

            return {
                'success': True,
                'hedge_position_side': hedge_position_side,
                'hedge_entry_price': hedge_entry_price,
                'sl_price': sl_price,
                'trigger_price': trigger_price,
                'sl_movement_percent': sl_movement_percent * 100,
                'trigger_movement_percent': trigger_movement_percent * 100,
                'error': None
            }

        except Exception as e:
            logger.error(f"Ошибка расчёта уровней хеджа: {e}")
            return {
                'success': False,
                'hedge_position_side': hedge_position_side,
                'hedge_entry_price': hedge_entry_price,
                'sl_price': None,
                'trigger_price': None,
                'sl_movement_percent': None,
                'trigger_movement_percent': None,
                'error': str(e)
            }