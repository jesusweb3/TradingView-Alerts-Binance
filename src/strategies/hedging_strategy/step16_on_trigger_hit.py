# src/strategies/hedging_strategy/step16_on_trigger_hit.py

from typing import Dict, Any, Callable, Coroutine
from src.utils.logger import get_logger

logger = get_logger(__name__)


class OnTriggerHit:
    """Обработка срабатывания TRIGGER - перенос стопа в профит"""

    def __init__(self, exchange, price_stream):
        self.exchange = exchange
        self.price_stream = price_stream
        self.symbol = exchange.symbol

    async def execute(
        self,
        current_price: float,
        trigger_price: float,
        hedge_entry_price: float,
        hedge_position_side: str,
        active_stop_order_id: str,
        tp_pnl: float,
        on_tp_callback: Callable[[float], Coroutine]
    ) -> Dict[str, Any]:
        """
        Обрабатывает срабатывание TRIGGER (PNL хеджа достиг +5%)

        Процесс:
        1. Отменить оба watch'а (SL + TRIGGER)
        2. Отменить первый SL ордер (старый SL больше не нужен)
        3. Рассчитать TP цену (где профит достигает tp_pnl%)
        4. Выставить новый SL на TP цену (в профите)
        5. Запустить watch на TP цену

        Args:
            current_price: Текущая цена при срабатывании TRIGGER
            trigger_price: Цена триггера которую достигли
            hedge_entry_price: ТВХ хеджа
            hedge_position_side: 'LONG' или 'SHORT'
            active_stop_order_id: ID текущего SL ордера для отмены
            tp_pnl: TP процент (обычно +2)
            on_tp_callback: Async callback при срабатывании TP

        Returns:
            {
                'success': bool,
                'trigger_hit_price': float,
                'cancel_watches_success': bool,
                'cancel_old_sl_success': bool,
                'tp_price': float | None,
                'new_stop_order_id': str | None,
                'tp_watch_started': bool,
                'error': str | None
            }
        """
        try:
            logger.info(
                f"TRIGGER СРАБАТИЛ! Цена достигла ${current_price:.2f} "
                f"(триггер был ${trigger_price:.2f}). "
                f"PNL по хеджу достиг {tp_pnl}% профита."
            )

            logger.info("Отменяем оба watch'а (SL + TRIGGER)")

            try:
                self.price_stream.cancel_all_watches()
                cancel_watches_success = True
                logger.info("Все watch'и отменены")
            except Exception as e:
                logger.error(f"Ошибка при отмене watch'ей: {e}")
                cancel_watches_success = False

            logger.info(f"Отменяем старый SL ордер (ID={active_stop_order_id})")

            cancel_old_sl_success = True
            if active_stop_order_id:
                try:
                    result = await self.exchange.cancel_stop_loss_order(
                        symbol=self.symbol,
                        order_id=active_stop_order_id
                    )

                    if result:
                        logger.info(f"Старый SL ордер {active_stop_order_id} отменен")
                    else:
                        logger.error(f"Не удалось отменить SL ордер {active_stop_order_id}")
                        cancel_old_sl_success = False

                except Exception as e:
                    logger.error(f"Ошибка при отмене SL ордера: {e}")
                    cancel_old_sl_success = False
            else:
                logger.warning("Active stop order ID не найден")
                cancel_old_sl_success = False

            logger.info("Рассчитываем новую цену стопа (TP в профите)")

            tp_price = self.exchange.calculate_new_stop_price(
                entry_price=hedge_entry_price,
                position_side=hedge_position_side,
                tp_pnl=tp_pnl
            )

            logger.info(
                f"TP цена рассчитана: ${tp_price:.2f} "
                f"(будет {tp_pnl}% профита для {hedge_position_side} хеджа)"
            )

            logger.info(f"Выставляем новый SL (TP) на цену ${tp_price:.2f}")

            new_stop_result = await self.exchange.place_stop_loss_order(
                symbol=self.symbol,
                position_side=hedge_position_side,
                stop_price=tp_price
            )

            if new_stop_result is None:
                logger.error("Не удалось выставить новый TP SL ордер")
                return {
                    'success': False,
                    'trigger_hit_price': current_price,
                    'cancel_watches_success': cancel_watches_success,
                    'cancel_old_sl_success': cancel_old_sl_success,
                    'tp_price': tp_price,
                    'new_stop_order_id': None,
                    'tp_watch_started': False,
                    'error': 'Не удалось выставить новый TP SL'
                }

            new_stop_order_id = new_stop_result.get('orderId')

            if not new_stop_order_id:
                logger.error("Новый TP SL выставлен, но не получен order_id")
                return {
                    'success': False,
                    'trigger_hit_price': current_price,
                    'cancel_watches_success': cancel_watches_success,
                    'cancel_old_sl_success': cancel_old_sl_success,
                    'tp_price': tp_price,
                    'new_stop_order_id': None,
                    'tp_watch_started': False,
                    'error': 'Order ID не получен для нового TP SL'
                }

            logger.info(
                f"Новый TP SL успешно выставлен: ID={new_stop_order_id}, цена=${tp_price:.2f}"
            )

            logger.info("Запускаем watch на TP цену")

            if hedge_position_side == 'LONG':
                tp_direction = 'long'
            else:
                tp_direction = 'short'

            self.price_stream.watch_price(
                target_price=tp_price,
                direction=tp_direction,
                on_reach=on_tp_callback,
                barrier_price=None,
                barrier_side=None
            )

            logger.info(
                f"Watch на TP запущен: ${tp_price:.2f} ({tp_direction}). "
                f"Ждём срабатывания TP для закрытия хеджа в профит."
            )

            return {
                'success': True,
                'trigger_hit_price': current_price,
                'cancel_watches_success': cancel_watches_success,
                'cancel_old_sl_success': cancel_old_sl_success,
                'tp_price': tp_price,
                'new_stop_order_id': str(new_stop_order_id),
                'tp_watch_started': True,
                'error': None
            }

        except Exception as e:
            logger.error(f"Ошибка обработки TRIGGER срабатывания: {e}")
            return {
                'success': False,
                'trigger_hit_price': current_price,
                'cancel_watches_success': False,
                'cancel_old_sl_success': False,
                'tp_price': None,
                'new_stop_order_id': None,
                'tp_watch_started': False,
                'error': str(e)
            }