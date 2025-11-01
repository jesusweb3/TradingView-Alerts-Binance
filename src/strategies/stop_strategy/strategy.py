# src/strategies/stop_strategy/strategy.py

import asyncio
from typing import Optional, Literal
from src.utils.logger import get_logger
from src.config.manager import config_manager
from src.binance.stop_client import StopBinanceClient
from src.binance.wss import BinancePriceStream

logger = get_logger(__name__)

Action = Literal["buy", "sell"]


class StopStrategy:
    """Торговая стратегия с трейлинг-стопами по PnL"""

    def __init__(self):
        binance_config = config_manager.get_binance_config()
        trading_config = config_manager.get_trading_config()

        self.symbol = trading_config['symbol']

        self.exchange = StopBinanceClient(
            api_key=binance_config['api_key'],
            secret=binance_config['secret'],
            position_size=binance_config['position_size'],
            leverage=binance_config['leverage'],
            symbol=self.symbol
        )

        self.last_action: Optional[Action] = None
        self.last_quantity: Optional[float] = None
        self.current_price: Optional[float] = None
        self._price_lock = asyncio.Lock()

        self.price_stream: Optional[BinancePriceStream] = None
        self._initialized = False

        self.active_stop_order_id: Optional[str] = None
        self.stop_watch_key: Optional[str] = None

    async def initialize(self):
        """Async инициализация стратегии"""
        if self._initialized:
            return

        await self.exchange.initialize()

        self.price_stream = BinancePriceStream(self.symbol, self._on_price_update)
        self.price_stream.start()

        await self._restore_state_after_restart()

        self._initialized = True
        logger.info("Stop стратегия инициализирована")

    def _on_price_update(self, price: float):
        """Обработчик обновления цены из WebSocket потока"""
        try:
            asyncio.create_task(self._async_price_update(price))
        except Exception as e:
            logger.error(f"Ошибка создания задачи обновления цены: {e}")

    async def _async_price_update(self, price: float):
        """Async обработка обновления цены"""
        try:
            async with self._price_lock:
                self.current_price = price
        except Exception as e:
            logger.error(f"Ошибка обработки обновления цены {price}: {e}")

    async def get_current_price(self) -> Optional[float]:
        """Возвращает текущую цену из WebSocket с fallback на последнюю известную"""
        async with self._price_lock:
            if self.current_price is not None:
                return self.current_price

        last_known_price = self.price_stream.get_last_price()
        if last_known_price is not None:
            logger.debug(f"Используем последнюю известную цену: ${last_known_price:.2f}")

        return last_known_price

    async def _restore_state_after_restart(self):
        """Восстанавливает состояние после перезапуска сервера"""
        try:
            current_position = await self.exchange.get_current_position(self.symbol)

            if not current_position:
                logger.info("Позиций не обнаружено при старте")
                return

            logger.info(f"Обнаружена открытая позиция: {current_position['side']} "
                        f"{current_position['size']} по ${current_position['entry_price']:.2f}")

            self.last_action = "buy" if current_position['side'] == "Buy" else "sell"
            self.last_quantity = current_position['size']
            logger.info(f"Восстановлен last_action={self.last_action}, last_quantity={self.last_quantity}")

        except Exception as e:
            logger.error(f"Ошибка восстановления состояния после перезапуска: {e}")

    @staticmethod
    def parse_message(message: str) -> Optional[Action]:
        """Парсит сообщение от TradingView в торговый сигнал"""
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
        """Проверяет нужно ли обрабатывать сигнал (фильтр дубликатов)"""
        if self.last_action is None:
            self.last_action = action
            return True

        if self.last_action == action:
            logger.info(f"Дублирующий сигнал {action} - игнорируется")
            return False

        self.last_action = action
        return True

    async def process_webhook(self, message: str) -> Optional[dict]:
        """Обрабатывает сообщение от webhook"""
        action = StopStrategy.parse_message(message)
        if not action:
            logger.info("Сигнал не распознан")
            return None

        if not self.should_process_signal(action):
            return {"status": "ignored", "message": "Сигнал отфильтрован как дубликат"}

        success = await self.process_signal(action)

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

    async def process_signal(self, action: Action) -> bool:
        """Обрабатывает торговый сигнал"""
        try:
            current_position = await self.exchange.get_current_position(self.symbol)
            position_size = self._get_position_size()

            logger.info(f"Начинаем обработку сигнала {action} для {self.symbol}")

            if current_position is None:
                success = await self._handle_no_position(action, position_size)
            else:
                success = await self._handle_existing_position(action, current_position, position_size)

            return success

        except Exception as e:
            logger.error(f"Ошибка обработки сигнала {action}: {e}")
            return False

    async def _handle_no_position(self, action: Action, position_size: float) -> bool:
        """Обрабатывает сигнал когда позиции нет"""
        return await self._open_new_position(action, position_size)

    async def _handle_existing_position(self, action: Action, current_position: dict, position_size: float) -> bool:
        """Обрабатывает сигнал когда позиция уже открыта"""
        current_side = current_position['side']

        position_matches_signal = (
                (action == "buy" and current_side == "Buy") or
                (action == "sell" and current_side == "Sell")
        )

        if position_matches_signal:
            logger.info(f"Позиция {self.symbol} уже в направлении {action} - пропускаем")
            return True

        return await self._reverse_position(position_size)

    @staticmethod
    def _get_position_size() -> float:
        """Получает размер позиции из конфигурации"""
        try:
            binance_config = config_manager.get_binance_config()
            position_size = binance_config.get('position_size')
            if position_size is None:
                raise ValueError("В конфигурации не найдено поле position_size")

            return float(position_size)
        except Exception as e:
            logger.error(f"Критическая ошибка получения размера позиции: {e}")
            raise RuntimeError(f"Не удалось загрузить размер позиции из конфигурации: {e}")

    async def _open_new_position(self, action: Action, position_size: float) -> bool:
        """Открывает новую позицию"""
        current_price = await self.get_current_price()

        if current_price is None:
            current_price = self.price_stream.get_last_price()
            if current_price is None:
                logger.error("Цена из WebSocket недоступна")
                return False
            logger.warning(f"Используем последнюю известную цену ${current_price:.2f}")

        quantity = self.exchange.calculate_quantity(self.symbol, position_size, current_price)
        self.last_quantity = quantity
        logger.info(f"Расчет количества для {self.symbol}: {quantity}")

        if action == "buy":
            success = await self.exchange.open_long_position(self.symbol, quantity)
            direction = "Long"
            position_side = "Buy"
        else:
            success = await self.exchange.open_short_position(self.symbol, quantity)
            direction = "Short"
            position_side = "Sell"

        if not success:
            logger.error(f"Не удалось открыть {direction} позицию {self.symbol}")
            return False

        logger.info(f"Позиция {direction} открыта для {self.symbol}")

        entry_price = await self.exchange.get_exact_entry_price(self.symbol)
        if entry_price:
            await self._start_stop_monitoring(position_side, entry_price)

        return True

    async def _reverse_position(self, position_size: float) -> bool:
        """Разворот позиции с суммированием объемов"""
        current_price = await self.get_current_price()

        if current_price is None:
            current_price = self.price_stream.get_last_price()
            if current_price is None:
                logger.error("Цена из WebSocket недоступна для разворота")
                return False
            logger.warning(f"Используем последнюю известную цену ${current_price:.2f} для разворота")

        await self.exchange.cancel_all_stops(self.symbol, "Buy" if self.last_action == "sell" else "Sell")

        new_quantity = self.exchange.calculate_quantity(self.symbol, position_size, current_price)

        if self.last_quantity is None:
            logger.warning("last_quantity не установлен, используем только новый объем")
            total_quantity = new_quantity * 2
        else:
            total_quantity = self.last_quantity + new_quantity

        logger.info(
            f"Разворот: предыдущий объем {self.last_quantity}, новый объем {new_quantity}, итого {total_quantity}")

        success = await self.exchange.reverse_position_fast(self.symbol, total_quantity)

        if success:
            self.last_quantity = new_quantity

            current_position = await self.exchange.get_current_position(self.symbol)
            if current_position:
                entry_price = current_position['entry_price']
                new_position_side = "Buy" if current_position['side'] == "Buy" else "Sell"
                await self._start_stop_monitoring(new_position_side, entry_price)

        return success

    async def _start_stop_monitoring(self, position_side: str, entry_price: float):
        """Запускает мониторинг стопа для текущей позиции"""
        try:
            stop_config = config_manager.get_trailing_stop_config()

            if not stop_config:
                logger.warning("Конфигурация стопа не загружена")
                return

            activation_percent = stop_config.get('activation_percent')
            stop_percent = stop_config.get('stop_percent')

            if activation_percent is None or stop_percent is None:
                logger.warning("Параметры стопа не найдены в конфигурации")
                return

            activation_price, stop_limit_price = self._calculate_stop_levels(
                entry_price, position_side, activation_percent, stop_percent
            )

            direction = 'long' if position_side == 'Buy' else 'short'

            logger.info(
                f"Запуск мониторинга: Цена входа ${entry_price:.2f}, "
                f"Активация на ${activation_price:.2f} ({activation_percent}% PnL), "
                f"Стоп на ${stop_limit_price:.2f} ({stop_percent}% PnL)"
            )

            callback = self._create_stop_callback(position_side, stop_limit_price)
            self.price_stream.watch_price(
                target_price=activation_price,
                direction=direction,
                on_reach=callback
            )

        except Exception as e:
            logger.error(f"Ошибка запуска мониторинга стопа: {e}")

    def _calculate_stop_levels(self, entry_price: float, position_side: str,
                               activation_percent: float, stop_percent: float) -> tuple:
        """
        Рассчитывает уровни цены для активации и стопа на основе PnL процентов

        Args:
            entry_price: Цена входа
            position_side: 'Buy' или 'Sell'
            activation_percent: PnL процент для активации стопа
            stop_percent: PnL процент для уровня стопа

        Returns:
            (activation_price, stop_limit_price)
        """
        leverage = self.exchange.leverage

        activation_movement = activation_percent / (100 * leverage)
        stop_movement = stop_percent / (100 * leverage)

        if position_side == 'Buy':
            activation_price = entry_price * (1 + activation_movement)
            stop_limit_price = entry_price * (1 + stop_movement)
        else:
            activation_price = entry_price * (1 - activation_movement)
            stop_limit_price = entry_price * (1 - stop_movement)

        return activation_price, stop_limit_price

    def _create_stop_callback(self, position_side: str, stop_limit_price: float):
        """Создаёт async callback для активации стопа"""

        async def callback(current_price: float) -> None:
            logger.debug(f"Активирован callback на цене ${current_price:.2f}")
            await self._on_activation_price_reached(position_side, stop_limit_price)

        return callback

    async def _on_activation_price_reached(self, position_side: str, stop_limit_price: float) -> None:
        """Колбек вызываемый когда цена достигает уровня активации"""
        try:
            logger.info(f"Цена активации достигнута, выставляем стоп-лимит ордер")

            side = 'SELL' if position_side == 'Buy' else 'BUY'
            current_position = await self.exchange.get_current_position(self.symbol)

            if not current_position:
                logger.warning("Позиция не найдена при активации стопа")
                return

            quantity = current_position['size']

            stop_price = stop_limit_price + 1 if position_side == 'Buy' else stop_limit_price - 1

            result = await self.exchange.place_stop_limit_order(
                symbol=self.symbol,
                side=side,
                quantity=quantity,
                stop_price=stop_price,
                limit_price=stop_limit_price
            )

            if result:
                self.active_stop_order_id = result.get('orderId')
                logger.info(f"Стоп-лимит ордер выставлен: ID={self.active_stop_order_id}, "
                            f"stop={stop_price}, limit={stop_limit_price}")
            else:
                logger.error("Не удалось выставить стоп-лимит ордер")

        except Exception as e:
            logger.error(f"Ошибка в _on_activation_price_reached: {e}")

    async def cleanup(self):
        """Очистка ресурсов при завершении стратегии"""
        try:
            logger.info("Начинается очистка ресурсов stop стратегии...")

            if self.price_stream:
                self.price_stream.stop()

            if self.exchange:
                await self.exchange.close()

            logger.info("Очистка ресурсов stop стратегии завершена")

        except Exception as e:
            logger.error(f"Ошибка при очистке ресурсов стратегии: {e}")

    def get_status(self) -> dict:
        """Возвращает текущий статус стратегии"""
        status = {
            'mode': 'stop',
            'exchange': self.exchange.name,
            'symbol': self.symbol,
            'last_action': self.last_action,
            'last_quantity': self.last_quantity,
            'current_price': self.current_price,
            'active_stop_order_id': self.active_stop_order_id
        }

        if self.price_stream:
            ws_stats = self.price_stream.get_connection_stats()
            status['websocket'] = {
                'is_running': ws_stats['is_running'],
                'is_connected': ws_stats['is_connected'],
                'is_healthy': self.price_stream.is_healthy(),
                'connection_count': ws_stats['connection_count'],
                'last_price': ws_stats['last_price'],
                'last_price_update': ws_stats['last_price_update'],
                'last_successful_connection': ws_stats['last_successful_connection']
            }
            if 'current_downtime_seconds' in ws_stats:
                status['websocket']['current_downtime_seconds'] = ws_stats['current_downtime_seconds']

        return status