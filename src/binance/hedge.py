# src/binance/hedge.py

import asyncio
from typing import Optional, Dict, Any
from dataclasses import dataclass, field
from src.utils.logger import get_logger
from src.config.manager import config_manager

logger = get_logger(__name__)


@dataclass
class PendingHedge:
    """Информация о готовящемся хедже"""
    symbol: str
    main_twx: float
    main_position_side: str
    activation_pnl_target: float
    sl_pnl_target: float
    activation_pnl: float
    sl_pnl: float
    tp_pnl: float


@dataclass
class ActiveHedge:
    """Информация об активном хедже"""
    symbol: str
    order_id: str
    hedge_twx: float
    hedge_position_side: str
    hedge_sl_price: float
    main_twx: float
    main_position_side: str
    activation_pnl: float
    sl_pnl: float
    tp_pnl: float
    leverage: int
    breakeven_sl_active: bool = field(default=False)


class HedgeManager:
    """Менеджер хеджирования с мониторингом PnL и управлением стопами"""

    def __init__(self, exchange_client):
        self.exchange_client = exchange_client
        self.leverage = exchange_client.leverage
        self.logger = get_logger(__name__)

        self.pending_hedge: Optional[PendingHedge] = None
        self.active_hedge: Optional[ActiveHedge] = None

        self.monitoring_active = False
        self.failed_hedges_count = 0
        self.hedging_enabled = True
        self.hedge_can_be_triggered_again = False

        self._placement_lock = asyncio.Lock()
        self._is_placing_hedge = False

    def start_monitoring(self, symbol: str, main_twx: float, position_side: str):
        """
        Начинает мониторинг основной позиции для активации хеджа

        Args:
            symbol: Торговый символ
            main_twx: TWX основной позиции (точка отсчета)
            position_side: 'Buy' для лонга, 'Sell' для шорта
        """
        config = config_manager.get_hedging_config()

        if not self.hedging_enabled:
            self.logger.info("Хеджирование отключено, мониторинг не запускается")
            return

        activation_pnl = config['activation_pnl']
        sl_pnl = config['sl_pnl']
        tp_pnl = config['tp_pnl']

        if position_side == 'Buy':
            activation_price = main_twx * (1 + (activation_pnl / (100 * self.leverage)))
            sl_price = main_twx * (1 + (sl_pnl / (100 * self.leverage)))
        else:
            activation_price = main_twx * (1 - (activation_pnl / (100 * self.leverage)))
            sl_price = main_twx * (1 - (sl_pnl / (100 * self.leverage)))

        self.monitoring_active = True
        self.hedge_can_be_triggered_again = False

        self.logger.info(
            f"Мониторинг хеджирования: основная {position_side} по {main_twx:.2f}. "
            f"Активация хеджа при основной PnL {activation_pnl}% (цена {activation_price:.2f}). "
            f"Первый SL хеджа при основной PnL {sl_pnl}% (цена {sl_price:.2f}). "
            f"Перенос SL в БУ при хедж PnL +{tp_pnl}%"
        )

        self.pending_hedge = PendingHedge(
            symbol=symbol,
            main_twx=main_twx,
            main_position_side=position_side,
            activation_pnl_target=activation_price,
            sl_pnl_target=sl_price,
            activation_pnl=activation_pnl,
            sl_pnl=sl_pnl,
            tp_pnl=tp_pnl
        )

    async def check_price_and_activate(self, current_price: float):
        """
        Проверяет цену и активирует хедж при необходимости

        Args:
            current_price: Текущая цена актива
        """
        if not self.monitoring_active or self.active_hedge or not self.pending_hedge:
            return

        if self._is_placing_hedge:
            return

        pending = self.pending_hedge
        activation_target = pending.activation_pnl_target
        position_side = pending.main_position_side

        should_activate = False
        if position_side == 'Buy' and current_price <= activation_target:
            should_activate = True
        elif position_side == 'Sell' and current_price >= activation_target:
            should_activate = True

        if should_activate:
            async with self._placement_lock:
                if self._is_placing_hedge or self.active_hedge:
                    return

                self._is_placing_hedge = True

            self.logger.info(f"Активируем хедж: цена {current_price:.2f} достигла {activation_target:.2f}")
            await self._place_hedge(pending, current_price)

    async def _place_hedge(self, pending: PendingHedge, signal_price: float):
        """
        Размещает хедж позицию со встроенным стопом

        Args:
            pending: Параметры хеджа
            signal_price: Цена в момент активации (используется как hedge_twx)
        """
        try:
            hedge_side = 'SELL' if pending.main_position_side == 'Buy' else 'BUY'
            hedge_position_side = 'SHORT' if pending.main_position_side == 'Buy' else 'LONG'

            position = await self.exchange_client.get_current_position(pending.symbol)
            if not position:
                self.logger.error("Позиция не найдена для размещения хеджа")
                return

            quantity = abs(position['size'])
            stop_price = pending.sl_pnl_target

            result = await self.exchange_client.open_position_with_sl(
                symbol=pending.symbol,
                side=hedge_side,
                quantity=quantity,
                stop_price=stop_price,
                position_side=hedge_position_side
            )

            if result:
                config = config_manager.get_hedging_config()
                trigger_pnl = config['trigger_pnl']

                if hedge_position_side == 'SHORT':
                    trigger_price = signal_price * (1 - (trigger_pnl / (100 * self.leverage)))
                    breakeven_price = signal_price * (1 - (pending.tp_pnl / (100 * self.leverage)))
                else:
                    trigger_price = signal_price * (1 + (trigger_pnl / (100 * self.leverage)))
                    breakeven_price = signal_price * (1 + (pending.tp_pnl / (100 * self.leverage)))

                self.active_hedge = ActiveHedge(
                    symbol=pending.symbol,
                    order_id=result['orderId'],
                    hedge_twx=signal_price,
                    hedge_position_side=hedge_position_side,
                    hedge_sl_price=stop_price,
                    main_twx=pending.main_twx,
                    main_position_side=pending.main_position_side,
                    activation_pnl=pending.activation_pnl,
                    sl_pnl=pending.sl_pnl,
                    tp_pnl=pending.tp_pnl,
                    leverage=self.leverage
                )
                self.pending_hedge = None
                self.logger.info(
                    f"Хедж размещен: {hedge_side} {quantity} по {signal_price:.2f}, "
                    f"первый SL {stop_price:.2f}. "
                    f"Перенос SL при хедж PnL +{trigger_pnl}% (цена {trigger_price:.2f}) "
                    f"на уровень {breakeven_price:.2f} (БУ +{pending.tp_pnl}%)"
                )

        except Exception as e:
            self.logger.error(f"Ошибка размещения хеджа: {e}")
        finally:
            self._is_placing_hedge = False

    async def check_hedge_pnl(self, current_price: float):
        """
        Мониторит PnL хеджа и переносит SL в БУ если нужно

        Args:
            current_price: Текущая цена актива
        """
        if not self.active_hedge:
            return

        config = config_manager.get_hedging_config()
        trigger_pnl = config['trigger_pnl']

        hedge = self.active_hedge
        hedge_pnl_percent = self._calculate_pnl(
            hedge.hedge_twx,
            current_price,
            hedge.hedge_position_side
        )

        if hedge_pnl_percent >= trigger_pnl and not hedge.breakeven_sl_active:
            self.logger.info(f"Хедж достиг +{hedge_pnl_percent:.2f}%, переносим SL в БУ")
            await self._move_sl_to_breakeven(hedge)

    @staticmethod
    def _calculate_pnl(entry_price: float, current_price: float, position_side: str) -> float:
        """
        Рассчитывает PnL в процентах

        Args:
            entry_price: Цена входа
            current_price: Текущая цена
            position_side: 'LONG' или 'SHORT'

        Returns:
            PnL в процентах
        """
        if position_side == 'LONG':
            pnl = ((current_price - entry_price) / entry_price) * 100
        else:
            pnl = ((entry_price - current_price) / entry_price) * 100

        return pnl

    async def _move_sl_to_breakeven(self, hedge: ActiveHedge):
        """
        Переносит SL хеджа в БУ на уровне tp_pnl% профита от точки входа хеджа

        Args:
            hedge: Активный хедж
        """
        try:
            if hedge.hedge_position_side == 'SHORT':
                breakeven_price = hedge.hedge_twx * (1 - (hedge.tp_pnl / (100 * hedge.leverage)))
            else:
                breakeven_price = hedge.hedge_twx * (1 + (hedge.tp_pnl / (100 * hedge.leverage)))

            self.logger.info(f"Рассчитанная цена БУ для хеджа: ${breakeven_price:.2f} (PnL +{hedge.tp_pnl}%)")

            success = await self.exchange_client.update_stop_loss(
                symbol=hedge.symbol,
                new_stop_price=breakeven_price,
                position_side=hedge.hedge_position_side
            )

            if success:
                hedge.hedge_sl_price = breakeven_price
                hedge.breakeven_sl_active = True
                self.logger.info(f"SL хеджа перенесен в БУ на {breakeven_price:.2f}")

        except Exception as e:
            self.logger.error(f"Ошибка переноса SL в БУ: {e}")

    async def check_hedge_closed(self, current_price: float) -> bool:
        """
        Проверяет закрылся ли хедж по цене стоп лосса

        Args:
            current_price: Текущая цена

        Returns:
            True если хедж был закрыт
        """
        if not self.active_hedge:
            return False

        hedge = self.active_hedge

        if hedge.hedge_position_side == 'SHORT':
            hedge_closed = current_price >= hedge.hedge_sl_price
        else:
            hedge_closed = current_price <= hedge.hedge_sl_price

        if hedge_closed:
            self.logger.info(f"Хедж позиция закрыта (цена {current_price:.2f} достигла SL {hedge.hedge_sl_price:.2f})")
            await self._process_hedge_closure(hedge)
            self.active_hedge = None
            return True

        return False

    async def _process_hedge_closure(self, hedge: ActiveHedge):
        """
        Обрабатывает закрытие хеджа и определяет убыточность

        Args:
            hedge: Закрытый хедж
        """
        config = config_manager.get_hedging_config()
        max_failures = config['max_failures']

        if hedge.breakeven_sl_active:
            self.logger.info(f"Хедж закрыт по второму SL (БУ/профит)")
        else:
            self.failed_hedges_count += 1
            self.logger.warning(f"Хедж закрыт по первому SL (убыток). Неудач: {self.failed_hedges_count}")

            if self.failed_hedges_count >= max_failures:
                self.hedging_enabled = False
                self.logger.error(
                    f"Хеджирование отключено: {self.failed_hedges_count} неудач (максимум: {max_failures})")

    def check_main_pnl_crossed_activation(self, current_price: float) -> bool:
        """
        Проверяет пересекла ли основная позиция границу активации (для гистерезиса)

        Args:
            current_price: Текущая цена

        Returns:
            True если пересекла выше (для лонга) или ниже (для шорта)
        """
        if not self.pending_hedge:
            return False

        pending = self.pending_hedge
        activation_target = pending.activation_pnl_target
        position_side = pending.main_position_side

        if position_side == 'Buy':
            return current_price > activation_target
        else:
            return current_price < activation_target

    def stop_monitoring(self):
        """Останавливает мониторинг"""
        self.monitoring_active = False
        self.pending_hedge = None
        self.logger.info("Мониторинг хеджирования остановлен")

    def reset_for_new_signal(self):
        """Сбрасывает состояние для нового сигнала"""
        self.pending_hedge = None
        self.active_hedge = None
        self.monitoring_active = False
        self.hedge_can_be_triggered_again = False
        self.hedging_enabled = True

    def get_status(self) -> Dict[str, Any]:
        """Возвращает статус хеджирования"""
        status = {
            'hedging_enabled': self.hedging_enabled,
            'failed_hedges_count': self.failed_hedges_count,
            'monitoring_active': self.monitoring_active,
            'hedge_can_be_triggered_again': self.hedge_can_be_triggered_again,
            'has_active_hedge': self.active_hedge is not None,
            'has_pending_hedge': self.pending_hedge is not None
        }

        if self.active_hedge:
            hedge = self.active_hedge
            status['active_hedge'] = {
                'symbol': hedge.symbol,
                'position_side': hedge.hedge_position_side,
                'hedge_twx': hedge.hedge_twx,
                'current_sl': hedge.hedge_sl_price,
                'breakeven_sl_active': hedge.breakeven_sl_active
            }

        return status