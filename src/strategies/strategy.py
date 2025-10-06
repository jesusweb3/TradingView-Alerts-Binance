# src/strategies/strategy.py

from typing import Optional, Literal
import threading
from src.utils.logger import get_logger
from src.config.manager import config_manager
from src.exchanges.binance.api import BinanceClient
from src.exchanges.binance.sl import StopManager
from src.exchanges.binance.wss import BinancePriceStream

logger = get_logger(__name__)

Action = Literal["buy", "sell"]


class Strategy:
    """Торговая стратегия со стопами"""

    def __init__(self):
        binance_config = config_manager.get_binance_config()
        trading_config = config_manager.get_trading_config()

        self.symbol = trading_config['symbol']

        self.exchange = BinanceClient(
            api_key=binance_config['api_key'],
            secret=binance_config['secret'],
            position_size=binance_config['position_size'],
            leverage=binance_config['leverage'],
            symbol=self.symbol
        )

        self.last_action: Optional[Action] = None
        self.current_price: Optional[float] = None
        self._price_lock = threading.Lock()

        self.stop_manager = StopManager(self.exchange)

        self.price_stream = BinancePriceStream(self.symbol, self._on_price_update)
        self.price_stream.start()

        self._restore_state_after_restart()

    def _on_price_update(self, price: float):
        """
        Обработчик обновления цены из WebSocket потока

        Args:
            price: Текущая цена актива
        """
        try:
            with self._price_lock:
                self.current_price = price

            self.stop_manager.check_price_and_activate(price)
        except Exception as e:
            logger.error(f"Ошибка обработки обновления цены {price}: {e}")

    def get_current_price(self) -> Optional[float]:
        """
        Возвращает текущую цену из WebSocket потока с fallback на последнюю известную

        Returns:
            Текущая цена или последняя известная цена если WebSocket временно недоступен
        """
        with self._price_lock:
            if self.current_price is not None:
                return self.current_price

        last_known_price = self.price_stream.get_last_price()
        if last_known_price is not None:
            logger.debug(f"Используем последнюю известную цену: ${last_known_price:.2f}")

        return last_known_price

    def _restore_state_after_restart(self):
        """
        Восстанавливает состояние после перезапуска сервера

        Логика:
        1. Проверяет есть ли открытая позиция
        2. Проверяет есть ли активные стоп-ордера
        3. Если позиция есть, стопа нет → восстанавливает мониторинг
        4. Устанавливает last_action на основе направления позиции
        """
        try:
            normalized_symbol = self.exchange.normalize_symbol(self.symbol)
            current_position = self.exchange.get_current_position(normalized_symbol)

            if not current_position:
                logger.info("Позиций не обнаружено при старте")
                return

            logger.info(f"Обнаружена открытая позиция: {current_position['side']} "
                        f"{current_position['size']} по ${current_position['entry_price']:.2f}")

            self.last_action = "buy" if current_position['side'] == "Buy" else "sell"

            open_orders = self.exchange.get_open_orders(normalized_symbol)

            has_stop_order = any(
                order.get('type') == 'STOP' and order.get('reduceOnly', False)
                for order in open_orders
            )

            if has_stop_order:
                logger.info("Обнаружен активный стоп-ордер, восстановление не требуется")
                return

            logger.info("Стоп-ордер не обнаружен, восстанавливаем мониторинг стопа")

            entry_price = current_position['entry_price']
            position_side = current_position['side']

            self.stop_manager.start_monitoring(normalized_symbol, entry_price, position_side)

            logger.info(f"Мониторинг стопа успешно восстановлен для {position_side} позиции")

        except Exception as e:
            logger.error(f"Ошибка восстановления состояния после перезапуска: {e}")

    @staticmethod
    def parse_message(message: str) -> Optional[Action]:
        """
        Парсит сообщение от TradingView в торговый сигнал

        Поддерживаемые форматы:
        - "buy" / "sell"
        - "BUY" / "SELL"
        - Любое сообщение содержащее "buy" или "sell"

        Args:
            message: Сообщение от TradingView

        Returns:
            "buy" или "sell", или None если действие не распознано
        """
        if not message or not isinstance(message, str):
            logger.warning("Пустое или некорректное сообщение")
            return None

        message = message.strip().lower()

        if "buy" in message:
            return "buy"
        elif "sell" in message:
            return "sell"
        else:
            logger.warning(f"В сообщении '{message}' не найдено действие buy/sell")
            return None

    def should_process_signal(self, action: Action) -> bool:
        """
        Проверяет нужно ли обрабатывать сигнал (фильтр дубликатов)

        Логика:
        - Первый сигнал всегда обрабатываем
        - Одинаковые сигналы подряд игнорируем (buy -> buy)
        - Противоположные сигналы обрабатываем (buy -> sell)
        """
        if self.last_action is None:
            self.last_action = action
            return True

        if self.last_action == action:
            logger.info(f"Дублирующий сигнал {action} - игнорируется")
            return False

        self.last_action = action
        return True

    def process_webhook(self, message: str) -> Optional[dict]:
        """
        Обрабатывает сообщение от webhook

        Args:
            message: Сообщение от TradingView

        Returns:
            Словарь с результатом обработки или None если сигнал не обработан
        """
        action = Strategy.parse_message(message)
        if not action:
            logger.info("Сигнал не распознан")
            return None

        if not self.should_process_signal(action):
            return {"status": "ignored", "message": "Сигнал отфильтрован как дубликат"}

        success = self.process_signal(action)

        if success:
            return {
                "status": "success",
                "signal": {
                    "symbol": self.symbol,
                    "action": action
                }
            }
        else:
            return {"status": "error", "message": "Ошибка обработки сигнала"}

    def process_signal(self, action: Action) -> bool:
        """
        Обрабатывает торговый сигнал с учетом стопа

        Логика:
        1. Отменяем активный стоп перед любой операцией
        2. Получаем символ из конфигурации
        3. Проверяем текущую позицию на бирже
        4. Если позиции нет - открываем новую
        5. Если есть позиция в том же направлении - игнорируем
        6. Если есть позиция в противоположном направлении - разворачиваем
        7. После успешной операции - запускаем мониторинг стопа
        """
        try:
            self.stop_manager.cancel_active_stop()

            normalized_symbol = self.exchange.normalize_symbol(self.symbol)
            current_position = self.exchange.get_current_position(normalized_symbol)
            position_size = self._get_position_size()

            logger.info(f"Начинаем обработку сигнала {action} для {self.symbol}")

            if current_position is None:
                success = self._handle_no_position(action, normalized_symbol, position_size)
            else:
                success = self._handle_existing_position(action, current_position, normalized_symbol)

            if success:
                self._start_stop_monitoring(action, normalized_symbol)

            return success

        except Exception as e:
            logger.error(f"Ошибка обработки сигнала {action}: {e}")
            return False

    def _handle_no_position(self, action: Action, normalized_symbol: str, position_size: float) -> bool:
        """
        Обрабатывает сигнал когда позиции нет

        Args:
            action: Торговый сигнал
            normalized_symbol: Нормализованный символ
            position_size: Размер позиции

        Returns:
            True если операция успешна
        """
        return self._open_new_position(action, normalized_symbol, position_size)

    def _handle_existing_position(self, action: Action, current_position: dict, normalized_symbol: str) -> bool:
        """
        Обрабатывает сигнал когда позиция уже открыта

        Args:
            action: Торговый сигнал
            current_position: Текущая позиция
            normalized_symbol: Нормализованный символ

        Returns:
            True если операция успешна
        """
        current_side = current_position['side']

        position_matches_signal = (
                (action == "buy" and current_side == "Buy") or
                (action == "sell" and current_side == "Sell")
        )

        if position_matches_signal:
            logger.info(f"Позиция {self.symbol} уже в направлении {action} - пропускаем")
            return True

        return self._reverse_position(normalized_symbol)

    def _start_stop_monitoring(self, action: Action, normalized_symbol: str):
        """
        Запускает мониторинг стопа после успешной торговой операции

        Args:
            action: Торговый сигнал
            normalized_symbol: Нормализованный символ
        """
        try:
            entry_price = self.exchange.get_exact_entry_price(normalized_symbol)

            if entry_price:
                position_side = 'Buy' if action == "buy" else 'Sell'
                self.stop_manager.start_monitoring(normalized_symbol, entry_price, position_side)
            else:
                logger.warning(f"Не удалось получить цену входа для {normalized_symbol}")

        except Exception as e:
            logger.error(f"Ошибка запуска мониторинга стопа: {e}")

    @staticmethod
    def _get_position_size() -> float:
        """Получает размер позиции из конфигурации биржи"""
        try:
            binance_config = config_manager.get_binance_config()
            position_size = binance_config.get('position_size')
            if position_size is None:
                raise ValueError("В конфигурации не найдено поле position_size")

            return float(position_size)
        except Exception as e:
            logger.error(f"Критическая ошибка получения размера позиции: {e}")
            raise RuntimeError(f"Не удалось загрузить размер позиции из конфигурации: {e}")

    def _open_new_position(self, action: Action, normalized_symbol: str, position_size: float) -> bool:
        """Открывает новую позицию используя цену из WebSocket"""
        with self._price_lock:
            current_price = self.current_price

        if current_price is None:
            current_price = self.price_stream.get_last_price()
            if current_price is None:
                logger.error("Цена из WebSocket недоступна")
                return False
            logger.warning(f"Используем последнюю известную цену ${current_price:.2f}")

        if action == "buy":
            success = self.exchange.open_long_position(
                normalized_symbol, position_size, current_price
            )
            direction = "Long"
        else:
            success = self.exchange.open_short_position(
                normalized_symbol, position_size, current_price
            )
            direction = "Short"

        if not success:
            logger.error(f"Не удалось открыть {direction} позицию {normalized_symbol}")
            return False

        entry_price = self.exchange.get_exact_entry_price(normalized_symbol)
        if entry_price:
            logger.info(f"Позиция {direction} открыта, точная цена входа ${entry_price:.2f}")

        return True

    def _reverse_position(self, normalized_symbol: str) -> bool:
        """
        Быстрый разворот позиции через удвоение объема

        Args:
            normalized_symbol: Нормализованный символ

        Returns:
            True если разворот успешен, False иначе
        """
        logger.info(f"Быстрый разворот позиции {normalized_symbol}")
        return self.exchange.reverse_position_fast(normalized_symbol)

    def cleanup(self):
        """Очистка ресурсов при завершении стратегии"""
        try:
            logger.info("Начинается очистка ресурсов стратегии...")

            if self.price_stream:
                self.price_stream.stop()

            if self.stop_manager:
                self.stop_manager.stop_monitoring()

            logger.info("Очистка ресурсов стратегии завершена")

        except Exception as e:
            logger.error(f"Ошибка при очистке ресурсов стратегии: {e}")

    def get_status(self) -> dict:
        """Возвращает текущий статус стратегии"""
        with self._price_lock:
            current_price = self.current_price

        status = {
            'exchange': self.exchange.name,
            'symbol': self.symbol,
            'last_action': self.last_action,
            'current_price': current_price
        }

        if self.price_stream:
            ws_stats = self.price_stream.get_connection_stats()
            status['websocket'] = {
                'is_running': ws_stats['is_running'],
                'is_healthy': self.price_stream.is_healthy(),
                'connection_count': ws_stats['connection_count'],
                'last_successful_connection': ws_stats['last_successful_connection']
            }
            if 'current_downtime_seconds' in ws_stats:
                status['websocket']['current_downtime_seconds'] = ws_stats['current_downtime_seconds']

        if self.stop_manager:
            status['stop_info'] = {
                'monitoring': self.stop_manager.is_monitoring(),
                'has_active_stop': self.stop_manager.has_active_stop(),
                'stop_details': self.stop_manager.get_stop_info()
            }

        return status