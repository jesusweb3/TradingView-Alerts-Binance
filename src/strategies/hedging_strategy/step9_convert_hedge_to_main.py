# src/strategies/hedging_strategy/step9_convert_hedge_to_main.py

from typing import Dict, Any, Optional
from src.utils.logger import get_logger

logger = get_logger(__name__)


class ConvertHedgeToMain:
    """Конвертация хеджа в основную позицию при получении нового сигнала"""

    def __init__(self, exchange, price_stream):
        self.exchange = exchange
        self.price_stream = price_stream
        self.symbol = exchange.symbol

    async def execute(
        self,
        current_price: Optional[float],
        hedge_entry_price: float,
        hedge_position_side: str,
        active_stop_order_id: Optional[str]
    ) -> Dict[str, Any]:
        """
        Конвертирует хедж позицию в основную при новом сигнале

        Процесс:
        1. Закрыть старую основную позицию
        2. Отменить все watch'и на цены
        3. Отменить стоп-лосс по хеджу
        4. Оставить хедж как новую основную
        5. Использовать текущую цену как виртуальный ТВХ

        Args:
            current_price: Текущая цена (для виртуального ТВХ)
            hedge_entry_price: ТВХ хеджа
            hedge_position_side: 'LONG' или 'SHORT'
            active_stop_order_id: ID активного стопа для отмены

        Returns:
            {
                'success': bool,
                'close_main_success': bool,
                'cancel_watches_success': bool,
                'cancel_stop_success': bool,
                'new_main_position_side': str,
                'new_main_entry_price': float,  # Виртуальный ТВХ
                'new_hedge_position_side': str,
                'failure_count': int,
                'error': str | None
            }
        """
        try:
            if current_price is None:
                logger.error("Текущая цена недоступна для конвертации")
                return {
                    'success': False,
                    'close_main_success': False,
                    'cancel_watches_success': False,
                    'cancel_stop_success': False,
                    'new_main_position_side': None,
                    'new_main_entry_price': None,
                    'new_hedge_position_side': None,
                    'failure_count': 0,
                    'error': 'Текущая цена недоступна'
                }

            logger.info("Начинаем конвертацию хеджа в основную позицию")

            current_main_position = await self.exchange.get_current_position(self.symbol)

            if not current_main_position:
                logger.warning("Старая основная позиция не найдена")
                close_main_success = False
            else:
                position_side = current_main_position['side']
                position_volume = current_main_position['size']

                logger.info(
                    f"Закрываем старую основную позицию {position_side} x{position_volume}"
                )

                close_main_success = await self.exchange.close_position(
                    symbol=self.symbol,
                    position_side=position_side,
                    quantity=position_volume
                )

                if close_main_success:
                    logger.info(f"Старая основная позиция {position_side} закрыта")
                else:
                    logger.error(f"Не удалось закрыть старую основную позицию {position_side}")

            logger.info("Отменяем все watch'и на цены")
            try:
                self.price_stream.cancel_all_watches()
                cancel_watches_success = True
                logger.info("Все watch'и отменены")
            except Exception as e:
                logger.error(f"Ошибка при отмене watch'ей: {e}")
                cancel_watches_success = False

            logger.info("Отменяем стоп-лосс по хеджу")
            cancel_stop_success = True

            if active_stop_order_id:
                try:
                    cancel_result = await self.exchange.cancel_stop_loss_order(
                        symbol=self.symbol,
                        order_id=active_stop_order_id
                    )

                    if cancel_result:
                        logger.info(f"Стоп-лосс {active_stop_order_id} отменен")
                    else:
                        logger.error(f"Не удалось отменить стоп-лосс {active_stop_order_id}")
                        cancel_stop_success = False

                except Exception as e:
                    logger.error(f"Ошибка при отмене стопа: {e}")
                    cancel_stop_success = False
            else:
                logger.warning("Активный стоп-лосс не найден")

            new_main_position_side = hedge_position_side
            new_hedge_position_side = 'SHORT' if hedge_position_side == 'LONG' else 'LONG'
            new_main_entry_price = current_price  # Виртуальный ТВХ

            logger.info(
                f"Конвертация завершена: "
                f"хедж {hedge_position_side} (ТВХ=${hedge_entry_price:.2f}) → "
                f"основная (виртуальный ТВХ=${new_main_entry_price:.2f}), "
                f"новый хедж будет {new_hedge_position_side}"
            )

            return {
                'success': close_main_success and cancel_watches_success and cancel_stop_success,
                'close_main_success': close_main_success,
                'cancel_watches_success': cancel_watches_success,
                'cancel_stop_success': cancel_stop_success,
                'new_main_position_side': new_main_position_side,
                'new_main_entry_price': new_main_entry_price,
                'new_hedge_position_side': new_hedge_position_side,
                'failure_count': 0,  # Обнуляем при конвертации
                'error': None
            }

        except Exception as e:
            logger.error(f"Ошибка при конвертации хеджа в основную позицию: {e}")
            return {
                'success': False,
                'close_main_success': False,
                'cancel_watches_success': False,
                'cancel_stop_success': False,
                'new_main_position_side': None,
                'new_main_entry_price': None,
                'new_hedge_position_side': None,
                'failure_count': 0,
                'error': str(e)
            }