# src/strategies/hedging_strategy/step13_place_initial_sl.py

from typing import Dict, Any
from src.utils.logger import get_logger

logger = get_logger(__name__)


class PlaceInitialSL:
    """Выставление первого STOP_MARKET ордера для хеджа"""

    def __init__(self, exchange):
        self.exchange = exchange
        self.symbol = exchange.symbol

    async def execute(
        self,
        hedge_position_side: str,
        sl_price: float
    ) -> Dict[str, Any]:
        """
        Выставляет первый стоп-лосс ордер (STOP_MARKET с closePosition=True)

        Args:
            hedge_position_side: 'LONG' или 'SHORT'
            sl_price: Цена активации стопа

        Returns:
            {
                'success': bool,
                'active_stop_order_id': str | None,
                'hedge_position_side': str,
                'sl_price': float,
                'order_details': Dict | None,
                'error': str | None
            }
        """
        try:
            logger.info(
                f"Выставляем первый SL ордер для {hedge_position_side} хеджа "
                f"на цену ${sl_price:.2f}"
            )

            result = await self.exchange.place_stop_loss_order(
                symbol=self.symbol,
                position_side=hedge_position_side,
                stop_price=sl_price
            )

            if result is None:
                logger.error(f"Не удалось выставить SL ордер для {hedge_position_side}")
                return {
                    'success': False,
                    'active_stop_order_id': None,
                    'hedge_position_side': hedge_position_side,
                    'sl_price': sl_price,
                    'order_details': None,
                    'error': 'Ошибка выставления ордера на бирже'
                }

            order_id = result.get('orderId')

            if not order_id:
                logger.error("Ордер выставлен, но не получен order_id")
                return {
                    'success': False,
                    'active_stop_order_id': None,
                    'hedge_position_side': hedge_position_side,
                    'sl_price': sl_price,
                    'order_details': result,
                    'error': 'Order ID не получен'
                }

            logger.info(
                f"SL ордер успешно выставлен: "
                f"ID={order_id}, {hedge_position_side}, цена=${sl_price:.2f}"
            )

            return {
                'success': True,
                'active_stop_order_id': str(order_id),
                'hedge_position_side': hedge_position_side,
                'sl_price': sl_price,
                'order_details': result,
                'error': None
            }

        except Exception as e:
            logger.error(f"Ошибка при выставлении SL ордера: {e}")
            return {
                'success': False,
                'active_stop_order_id': None,
                'hedge_position_side': hedge_position_side,
                'sl_price': sl_price,
                'order_details': None,
                'error': str(e)
            }