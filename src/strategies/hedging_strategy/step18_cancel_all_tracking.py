# src/strategies/hedging_strategy/step18_cancel_all_tracking.py

from typing import Dict, Any, Optional
from src.utils.logger import get_logger

logger = get_logger(__name__)


class CancelAllTracking:
    """Отмена всех watch'ей и стопов при новом сигнале"""

    def __init__(self, exchange, price_stream):
        self.exchange = exchange
        self.price_stream = price_stream
        self.symbol = exchange.symbol

    async def execute(
        self,
        active_stop_order_id: Optional[str]
    ) -> Dict[str, Any]:
        """
        Отменяет все текущие watch'и и стопы при приходе нового сигнала

        Процесс:
        1. Отменить все watch'и в price_stream
        2. Если есть активный SL → отменить его на бирже
        3. Обнулить active_stop_order_id

        Args:
            active_stop_order_id: ID активного SL ордера (если есть)

        Returns:
            {
                'success': bool,
                'cancel_watches_success': bool,
                'cancel_stop_success': bool,
                'active_stop_order_id': None,
                'error': str | None
            }
        """
        try:
            logger.info("Отменяем ВСЕ watch'и на отслеживаемые цены")

            cancel_watches_success = True
            try:
                self.price_stream.cancel_all_watches()
                logger.info("Все watch'и успешно отменены")
            except Exception as e:
                logger.error(f"Ошибка при отмене watch'ей: {e}")
                cancel_watches_success = False

            logger.info("Отменяем активный стоп-лосс ордер (если есть)")

            cancel_stop_success = True
            if active_stop_order_id:
                try:
                    result = await self.exchange.cancel_stop_loss_order(
                        symbol=self.symbol,
                        order_id=active_stop_order_id
                    )

                    if result:
                        logger.info(f"Стоп-лосс ордер {active_stop_order_id} отменен")
                    else:
                        logger.warning(f"Не удалось отменить стоп-лосс ордер {active_stop_order_id}")
                        cancel_stop_success = False

                except Exception as e:
                    logger.error(f"Ошибка при отмене стоп-лосса: {e}")
                    cancel_stop_success = False
            else:
                logger.info("Активный стоп-лосс не найден (не выставлялся)")

            logger.info("Обнуляем active_stop_order_id")

            return {
                'success': cancel_watches_success and cancel_stop_success,
                'cancel_watches_success': cancel_watches_success,
                'cancel_stop_success': cancel_stop_success,
                'active_stop_order_id': None,
                'error': None
            }

        except Exception as e:
            logger.error(f"Ошибка при отмене всех tracking'ов: {e}")
            return {
                'success': False,
                'cancel_watches_success': False,
                'cancel_stop_success': False,
                'active_stop_order_id': None,
                'error': str(e)
            }