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

        Формулы:
        LONG хедж:
          - SL = entry + (sl_pnl / leverage)      # sl_pnl отрицательный (-3)
          - TRIGGER = entry + (trigger_pnl / leverage)  # trigger_pnl положительный (+3)

        SHORT хедж:
          - SL = entry - (sl_pnl / leverage)      # sl_pnl отрицательный (-3)
          - TRIGGER = entry - (trigger_pnl / leverage)  # trigger_pnl положительный (+3)

        Примеры (leverage=4):
        SHORT хедж, entry=3950, sl_pnl=-3%, trigger_pnl=3%:
          - SL = 3950 - (-3/4) = 3950 + 0.75% = 3979.62
          - TRIGGER = 3950 - (3/4) = 3950 - 0.75% = 3920.37

        LONG хедж, entry=3950, sl_pnl=-3%, trigger_pnl=3%:
          - SL = 3950 + (-3/4) = 3950 - 0.75% = 3920.38
          - TRIGGER = 3950 + (3/4) = 3950 + 0.75% = 3979.62

        Args:
            hedge_entry_price: ТВХ хеджа
            hedge_position_side: 'LONG' или 'SHORT'
            leverage: Кредитное плечо
            sl_pnl: SL в процентах PNL (обычно -3)
            trigger_pnl: TRIGGER в процентах PNL (обычно +3)

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

            sl_movement = sl_pnl / leverage
            trigger_movement = trigger_pnl / leverage

            if hedge_position_side == 'LONG':
                sl_price = hedge_entry_price + sl_movement
                trigger_price = hedge_entry_price + trigger_movement
                sl_direction = 'вверх'
                trigger_direction = 'вверх'
            else:  # SHORT
                sl_price = hedge_entry_price - sl_movement
                trigger_price = hedge_entry_price - trigger_movement
                sl_direction = 'вниз'
                trigger_direction = 'вниз'

            sl_movement_percent = (sl_movement / hedge_entry_price) * 100
            trigger_movement_percent = (trigger_movement / hedge_entry_price) * 100

            logger.info(
                f"SL уровень (PNL {sl_pnl}%): ${sl_price:.2f} "
                f"({sl_direction}, {sl_movement_percent:.3f}%)"
            )
            logger.info(
                f"TRIGGER уровень (PNL {trigger_pnl}%): ${trigger_price:.2f} "
                f"({trigger_direction}, {trigger_movement_percent:.3f}%)"
            )

            return {
                'success': True,
                'hedge_position_side': hedge_position_side,
                'hedge_entry_price': hedge_entry_price,
                'sl_price': sl_price,
                'trigger_price': trigger_price,
                'sl_movement_percent': sl_movement_percent,
                'trigger_movement_percent': trigger_movement_percent,
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