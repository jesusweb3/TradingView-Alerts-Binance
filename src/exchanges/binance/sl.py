# src/exchanges/binance/sl.py

from typing import Optional, Dict, Any
from dataclasses import dataclass
import threading
from src.utils.logger import get_logger
from src.config.manager import config_manager

logger = get_logger(__name__)


@dataclass
class PendingStop:
    """Информация о готовящемся стоп ордере"""
    symbol: str
    entry_price: float
    activation_price: float
    stop_price: float
    limit_price: float
    position_side: str


@dataclass
class ActiveStop:
    """Информация об активном стоп ордере"""
    order_id: str
    symbol: str
    entry_price: float
    activation_price: float
    stop_price: float
    limit_price: float
    position_side: str


class StopManager:
    """Менеджер стопов для Binance с расчетом по PnL"""

    def __init__(self, exchange_client):
        self.exchange_client = exchange_client
        self.active_stop: Optional[ActiveStop] = None
        self.monitoring_active = False
        self.pending_stop: Optional[PendingStop] = None
        self.leverage = exchange_client.leverage
        self._placement_lock = threading.Lock()
        self._is_placing_order = False

    def start_monitoring(self, symbol: str, entry_price: float, position_side: str):
        """
        Начинает мониторинг для установки стопа с расчетом по PnL

        Args:
            symbol: Торговый символ
            entry_price: Цена входа в позицию
            position_side: 'Buy' для лонга, 'Sell' для шорта
        """
        config = config_manager.get_trailing_stop_config()

        if not config['enabled']:
            logger.info("Стоп отключен")
            return

        activation_percent = config['activation_percent']
        stop_percent = config['stop_percent']

        if position_side == 'Buy':
            activation_price = entry_price * (1 + (activation_percent / (100 * self.leverage)))
            limit_price = entry_price * (1 + (stop_percent / (100 * self.leverage)))
            stop_price = limit_price + 1
        else:
            activation_price = entry_price * (1 - (activation_percent / (100 * self.leverage)))
            limit_price = entry_price * (1 - (stop_percent / (100 * self.leverage)))
            stop_price = limit_price - 1

        self.monitoring_active = True
        self._is_placing_order = False

        logger.info(
            f"Запуск мониторинга стопа: Цена входа: ${entry_price:.2f}. "
            f"PnL для активации: {activation_percent}% -> цена ${activation_price:.2f}. "
            f"PnL для стопа: {stop_percent}% -> цена ${limit_price:.2f}. "
            f"Триггер цена: ${stop_price:.2f}"
        )

        self.pending_stop = PendingStop(
            symbol=symbol,
            entry_price=entry_price,
            activation_price=activation_price,
            stop_price=stop_price,
            limit_price=limit_price,
            position_side=position_side
        )

    def check_price_and_activate(self, current_price: float):
        """
        Проверяет текущую цену и активирует стоп при необходимости

        Args:
            current_price: Текущая цена актива
        """
        if not self.monitoring_active or self.active_stop or not self.pending_stop:
            return

        if self._is_placing_order:
            return

        activation_price = self.pending_stop.activation_price
        position_side = self.pending_stop.position_side

        should_activate = False
        if position_side == 'Buy' and current_price >= activation_price:
            should_activate = True
        elif position_side == 'Sell' and current_price <= activation_price:
            should_activate = True

        if should_activate:
            with self._placement_lock:
                if self._is_placing_order or self.active_stop:
                    return

                self._is_placing_order = True

            logger.info(f"Цена достигла активации {activation_price:.2f}, размещаем стоп ордер")
            self._place_stop_order(self.pending_stop)

    def _place_stop_order(self, pending: PendingStop):
        """
        Размещает стоп ордер на бирже

        Args:
            pending: Параметры стоп ордера
        """
        try:
            side = 'SELL' if pending.position_side == 'Buy' else 'BUY'

            position = self.exchange_client.get_current_position(pending.symbol)
            if not position:
                logger.error("Позиция не найдена для установки стопа")
                return

            quantity = abs(position['size'])

            result = self.exchange_client.place_stop_limit_order(
                symbol=pending.symbol,
                side=side,
                quantity=quantity,
                stop_price=pending.stop_price,
                limit_price=pending.limit_price
            )

            if result:
                self.active_stop = ActiveStop(
                    order_id=result['orderId'],
                    symbol=pending.symbol,
                    entry_price=pending.entry_price,
                    activation_price=pending.activation_price,
                    stop_price=pending.stop_price,
                    limit_price=pending.limit_price,
                    position_side=pending.position_side
                )

                self.pending_stop = None

        except Exception as e:
            logger.error(f"Ошибка размещения стоп ордера: {e}")
        finally:
            self._is_placing_order = False

    def cancel_active_stop(self):
        """Отменяет активный стоп ордер"""
        if self.active_stop:
            try:
                logger.info(f"Отмена стоп ордера {self.active_stop.order_id} для {self.active_stop.symbol}")

                success = self.exchange_client.cancel_stop_order(
                    self.active_stop.symbol,
                    self.active_stop.order_id
                )

                if success:
                    logger.info(f"Стоп ордер {self.active_stop.order_id} успешно отменен")
                else:
                    logger.warning(f"Не удалось отменить стоп ордер {self.active_stop.order_id}")

                self.active_stop = None

            except Exception as e:
                logger.error(f"Ошибка отмены стоп ордера: {e}")

        self.monitoring_active = False
        self.pending_stop = None
        self._is_placing_order = False

    def stop_monitoring(self):
        """Останавливает мониторинг без отмены ордеров"""
        self.monitoring_active = False
        self.pending_stop = None
        self._is_placing_order = False
        logger.info("Мониторинг стопа остановлен")

    def has_active_stop(self) -> bool:
        """Проверяет есть ли активный стоп ордер"""
        return self.active_stop is not None

    def is_monitoring(self) -> bool:
        """Проверяет активен ли мониторинг"""
        return self.monitoring_active

    def get_stop_info(self) -> Optional[Dict[str, Any]]:
        """Возвращает информацию об активном стопе"""
        if self.active_stop:
            return {
                'order_id': self.active_stop.order_id,
                'symbol': self.active_stop.symbol,
                'entry_price': self.active_stop.entry_price,
                'activation_price': self.active_stop.activation_price,
                'stop_price': self.active_stop.stop_price,
                'limit_price': self.active_stop.limit_price,
                'position_side': self.active_stop.position_side
            }
        return None