# src/strategies/take_strategy/strategy.py

import asyncio
from typing import Optional, Literal, Tuple
from src.utils.logger import get_logger
from src.config.manager import config_manager
from src.binance.take_client import TakeBinanceClient
from src.binance.wss import BinancePriceStream

logger = get_logger(__name__)

Action = Literal["buy", "sell"]


class TakeStrategy:
    """Take стратегия: открытие позиции и выставление двух уровней TP"""

    def __init__(self):
        binance_config = config_manager.get_binance_config()
        trading_config = config_manager.get_trading_config()
        take_config = config_manager.get_take_strategy_config()

        self.symbol = trading_config['symbol']
        self.leverage = binance_config['leverage']

        self.tp1_percent = take_config['tp1']
        self.qty1_percent = take_config['qty1']
        self.tp2_percent = take_config['tp2']
        self.qty2_percent = take_config['qty2']

        self.exchange = TakeBinanceClient(
            api_key=binance_config['api_key'],
            secret=binance_config['secret'],
            position_size=binance_config['position_size'],
            leverage=self.leverage,
            symbol=self.symbol
        )

        self.last_action: Optional[Action] = None
        self.last_quantity: Optional[float] = None
        self.current_price: Optional[float] = None
        self._price_lock = asyncio.Lock()

        self.price_stream: Optional[BinancePriceStream] = None
        self._initialized = False

    async def initialize(self):
        """Async инициализация стратегии"""
        if self._initialized:
            return

        await self.exchange.initialize()

        self.price_stream = BinancePriceStream(self.symbol, self._on_price_update)
        self.price_stream.start()

        await self._restore_state_after_restart()

        self._initialized = True
        logger.info("Take стратегия инициализирована")

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
        action = TakeStrategy.parse_message(message)
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
        """Обрабатывает сигнал когда позиции нет: открывает новую + выставляет TP"""
        try:
            logger.info("Позиции нет - открываем новую")
            current_price = await self.get_current_price()

            if current_price is None:
                current_price = self.price_stream.get_last_price()
                if current_price is None:
                    logger.error("Цена из WebSocket недоступна")
                    return False
                logger.warning(f"Используем последнюю известную цену ${current_price:.2f}")

            quantity = self.exchange.calculate_quantity(self.symbol, position_size, current_price)
            self.last_quantity = quantity

            logger.info(f"Рассчитано количество для {self.symbol}: {quantity}")

            if action == "buy":
                success = await self.exchange.open_long_position(self.symbol, quantity)
                position_side = "Buy"
            else:
                success = await self.exchange.open_short_position(self.symbol, quantity)
                position_side = "Sell"

            if not success:
                logger.error(f"Не удалось открыть позицию {self.symbol}")
                return False

            logger.info(f"Позиция {position_side} открыта для {self.symbol}")

            entry_price = await self.exchange.get_exact_entry_price(self.symbol)
            if entry_price is None:
                logger.error("Не удалось получить точный TVX")
                return False

            logger.info(f"Точный TVX получен: ${entry_price:.2f}")

            await self._place_tp_orders(entry_price, quantity, position_side)

            return True

        except Exception as e:
            logger.error(f"Ошибка обработки открытия новой позиции: {e}")
            return False

    async def _handle_existing_position(self, action: Action, current_position: dict,
                                        position_size: float) -> bool:
        """Обрабатывает сигнал когда позиция уже открыта: разворот + TP"""
        try:
            logger.info("Позиция уже открыта - готовимся к развороту")

            old_side = current_position['side']
            old_quantity = current_position['size']

            logger.info(f"Старая позиция: {old_side} x{old_quantity}")

            logger.info("Отменяем все старые TP ордера перед разворотом")
            cancel_success = await self.exchange.cancel_all_limit_orders(self.symbol)

            if not cancel_success:
                logger.warning("Не удалось отменить все старые TP ордера, продолжаем...")

            current_price = await self.get_current_price()

            if current_price is None:
                current_price = self.price_stream.get_last_price()
                if current_price is None:
                    logger.error("Цена из WebSocket недоступна для разворота")
                    return False
                logger.warning(f"Используем последнюю известную цену ${current_price:.2f}")

            new_quantity = self.exchange.calculate_quantity(self.symbol, position_size, current_price)

            if self.last_quantity is None:
                logger.warning("last_quantity не установлен, используем только новый объем")
                total_quantity = new_quantity * 2
            else:
                total_quantity = self.last_quantity + new_quantity

            logger.info(
                f"Разворот: предыдущий объем {self.last_quantity}, "
                f"новый объем {new_quantity}, итого {total_quantity}"
            )

            success = await self.exchange.reverse_position_fast(self.symbol, total_quantity)

            if not success:
                logger.error("Не удалось развернуть позицию")
                return False

            self.last_quantity = new_quantity
            logger.info(f"Позиция развернута, сохранен новый last_quantity: {self.last_quantity}")

            entry_price = await self.exchange.get_exact_entry_price(self.symbol)
            if entry_price is None:
                logger.error("Не удалось получить точный TVX после разворота")
                return False

            logger.info(f"Новый TVX получен: ${entry_price:.2f}")

            new_side = "Buy" if action == "buy" else "Sell"
            await self._place_tp_orders(entry_price, total_quantity, new_side)

            return True

        except Exception as e:
            logger.error(f"Ошибка разворота позиции: {e}")
            return False

    async def _place_tp_orders(self, entry_price: float, total_quantity: float,
                               position_side: str) -> bool:
        """Рассчитывает и выставляет два TP лимитных ордера"""
        try:
            logger.info(
                f"Рассчитываем TP уровни: entry=${entry_price:.2f}, "
                f"qty={total_quantity}, side={position_side}"
            )

            tp1_level, tp2_level = self.exchange.calculate_tp_levels(
                entry_price=entry_price,
                tp1_percent=self.tp1_percent,
                tp2_percent=self.tp2_percent,
                side=position_side
            )

            logger.info(f"TP уровни рассчитаны: TP1=${tp1_level:.2f}, TP2=${tp2_level:.2f}")

            qty1, qty2 = self.exchange.calculate_tp_quantities(
                total_quantity=total_quantity,
                qty1_percent=self.qty1_percent,
                qty2_percent=self.qty2_percent,
                symbol=self.symbol
            )

            logger.info(f"TP количества рассчитаны: qty1={qty1}, qty2={qty2}")

            close_side = "SELL" if position_side == "Buy" else "BUY"

            logger.info(f"Выставляем TP1: side={close_side}, qty={qty1}, price=${tp1_level:.2f}")

            result1 = await self.exchange.place_limit_order_reduce_only(
                symbol=self.symbol,
                side=close_side,
                quantity=qty1,
                price=tp1_level
            )

            if result1 is None:
                logger.error("Не удалось выставить TP1 ордер")
                return False

            logger.info(f"TP1 ордер выставлен успешно")

            logger.info(f"Выставляем TP2: side={close_side}, qty={qty2}, price=${tp2_level:.2f}")

            result2 = await self.exchange.place_limit_order_reduce_only(
                symbol=self.symbol,
                side=close_side,
                quantity=qty2,
                price=tp2_level
            )

            if result2 is None:
                logger.error("Не удалось выставить TP2 ордер")
                return False

            logger.info(f"TP2 ордер выставлен успешно")

            logger.info(
                f"Оба TP ордера выставлены: "
                f"TP1=${tp1_level:.2f} x{qty1}, TP2=${tp2_level:.2f} x{qty2}"
            )

            return True

        except Exception as e:
            logger.error(f"Ошибка выставления TP ордеров: {e}")
            return False

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

    async def cleanup(self):
        """Очистка ресурсов при завершении стратегии"""
        try:
            logger.info("Начинается очистка ресурсов take стратегии...")

            if self.price_stream:
                self.price_stream.stop()

            if self.exchange:
                await self.exchange.close()

            logger.info("Очистка ресурсов take стратегии завершена")

        except Exception as e:
            logger.error(f"Ошибка при очистке ресурсов стратегии: {e}")

    def get_status(self) -> dict:
        """Возвращает текущий статус стратегии"""
        status = {
            'mode': 'take',
            'exchange': self.exchange.name,
            'symbol': self.symbol,
            'last_action': self.last_action,
            'last_quantity': self.last_quantity,
            'current_price': self.current_price,
            'tp1_percent': self.tp1_percent,
            'qty1_percent': self.qty1_percent,
            'tp2_percent': self.tp2_percent,
            'qty2_percent': self.qty2_percent
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