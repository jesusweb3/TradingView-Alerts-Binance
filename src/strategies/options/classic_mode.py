# src/strategies/options/classic_mode.py

import asyncio
from typing import Optional, Literal
from src.utils.logger import get_logger
from src.config.manager import config_manager
from src.exchanges.binance.api import BinanceClient
from src.exchanges.binance.wss import BinancePriceStream

logger = get_logger(__name__)

Action = Literal["buy", "sell"]


class ClassicStrategy:
    """Классическая торговая стратегия без стопов"""

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
        logger.info("Классическая стратегия инициализирована")

    def _on_price_update(self, price: float):
        """
        Обработчик обновления цены из WebSocket потока

        Args:
            price: Текущая цена актива
        """
        try:
            asyncio.create_task(self._async_price_update(price))
        except Exception as e:
            logger.error(f"Ошибка создания задачи обновления цены: {e}")

    async def _async_price_update(self, price: float):
        """
        Async обработка обновления цены

        Args:
            price: Текущая цена актива
        """
        try:
            async with self._price_lock:
                self.current_price = price
        except Exception as e:
            logger.error(f"Ошибка обработки обновления цены {price}: {e}")

    async def get_current_price(self) -> Optional[float]:
        """
        Возвращает текущую цену из WebSocket потока с fallback на последнюю известную

        Returns:
            Текущая цена или последняя известная цена если WebSocket временно недоступен
        """
        async with self._price_lock:
            if self.current_price is not None:
                return self.current_price

        last_known_price = self.price_stream.get_last_price()
        if last_known_price is not None:
            logger.debug(f"Используем последнюю известную цену: ${last_known_price:.2f}")

        return last_known_price

    async def _restore_state_after_restart(self):
        """
        Восстанавливает состояние после перезапуска сервера

        Логика:
        1. Проверяет есть ли открытая позиция
        2. Устанавливает last_action на основе направления позиции
        3. Восстанавливает last_quantity из текущей позиции
        """
        try:
            normalized_symbol = self.exchange.normalize_symbol(self.symbol)
            current_position = await self.exchange.get_current_position(normalized_symbol)

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

    async def process_webhook(self, message: str) -> Optional[dict]:
        """
        Обрабатывает сообщение от webhook

        Args:
            message: Сообщение от TradingView

        Returns:
            Словарь с результатом обработки или None если сигнал не обработан
        """
        action = ClassicStrategy.parse_message(message)
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
        """
        Обрабатывает торговый сигнал

        Логика:
        1. Получаем символ из конфигурации
        2. Проверяем текущую позицию на бирже
        3. Если позиции нет - открываем новую
        4. Если есть позиция в том же направлении - игнорируем
        5. Если есть позиция в противоположном направлении - разворачиваем
        """
        try:
            normalized_symbol = self.exchange.normalize_symbol(self.symbol)
            current_position = await self.exchange.get_current_position(normalized_symbol)
            position_size = self._get_position_size()

            logger.info(f"Начинаем обработку сигнала {action} для {self.symbol}")

            if current_position is None:
                success = await self._handle_no_position(action, normalized_symbol, position_size)
            else:
                success = await self._handle_existing_position(action, current_position, normalized_symbol, position_size)

            return success

        except Exception as e:
            logger.error(f"Ошибка обработки сигнала {action}: {e}")
            return False

    async def _handle_no_position(self, action: Action, normalized_symbol: str, position_size: float) -> bool:
        """
        Обрабатывает сигнал когда позиции нет

        Args:
            action: Торговый сигнал
            normalized_symbol: Нормализованный символ
            position_size: Размер позиции

        Returns:
            True если операция успешна
        """
        return await self._open_new_position(action, normalized_symbol, position_size)

    async def _handle_existing_position(self, action: Action, current_position: dict, normalized_symbol: str, position_size: float) -> bool:
        """
        Обрабатывает сигнал когда позиция уже открыта

        Args:
            action: Торговый сигнал
            current_position: Текущая позиция
            normalized_symbol: Нормализованный символ
            position_size: Размер позиции

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

        return await self._reverse_position(normalized_symbol, position_size)

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

    async def _open_new_position(self, action: Action, normalized_symbol: str, position_size: float) -> bool:
        """Открывает новую позицию используя цену из WebSocket"""
        current_price = await self.get_current_price()

        if current_price is None:
            current_price = self.price_stream.get_last_price()
            if current_price is None:
                logger.error("Цена из WebSocket недоступна")
                return False
            logger.warning(f"Используем последнюю известную цену ${current_price:.2f}")

        if action == "buy":
            success = await self.exchange.open_long_position(
                normalized_symbol, position_size, current_price
            )
            direction = "Long"
        else:
            success = await self.exchange.open_short_position(
                normalized_symbol, position_size, current_price
            )
            direction = "Short"

        if not success:
            logger.error(f"Не удалось открыть {direction} позицию {normalized_symbol}")
            return False

        new_quantity = self.exchange.calculate_quantity(normalized_symbol, position_size, current_price)
        self.last_quantity = new_quantity
        logger.info(f"Сохранен last_quantity: {self.last_quantity}")

        entry_price = await self.exchange.get_exact_entry_price(normalized_symbol)
        if entry_price:
            logger.info(f"Позиция {direction} открыта, точная цена входа ${entry_price:.2f}")

        return True

    async def _reverse_position(self, normalized_symbol: str, position_size: float) -> bool:
        """
        Разворот позиции с суммированием объемов

        Args:
            normalized_symbol: Нормализованный символ
            position_size: Размер позиции из конфига

        Returns:
            True если разворот успешен, False иначе
        """
        current_price = await self.get_current_price()

        if current_price is None:
            current_price = self.price_stream.get_last_price()
            if current_price is None:
                logger.error("Цена из WebSocket недоступна для разворота")
                return False
            logger.warning(f"Используем последнюю известную цену ${current_price:.2f} для разворота")

        new_quantity = self.exchange.calculate_quantity(normalized_symbol, position_size, current_price)

        if self.last_quantity is None:
            logger.warning("last_quantity не установлен, используем только новый объем")
            total_quantity = new_quantity * 2
        else:
            total_quantity = self.last_quantity + new_quantity

        logger.info(f"Разворот: предыдущий объем {self.last_quantity}, новый объем {new_quantity}, итого {total_quantity}")

        success = await self.exchange.reverse_position_fast(normalized_symbol, total_quantity)

        if success:
            self.last_quantity = new_quantity
            logger.info(f"Сохранен новый last_quantity: {self.last_quantity}")

        return success

    async def cleanup(self):
        """Очистка ресурсов при завершении стратегии"""
        try:
            logger.info("Начинается очистка ресурсов классической стратегии...")

            if self.price_stream:
                self.price_stream.stop()

            if self.exchange:
                await self.exchange.close()

            logger.info("Очистка ресурсов классической стратегии завершена")

        except Exception as e:
            logger.error(f"Ошибка при очистке ресурсов стратегии: {e}")

    def get_status(self) -> dict:
        """Возвращает текущий статус стратегии"""
        status = {
            'mode': 'classic',
            'exchange': self.exchange.name,
            'symbol': self.symbol,
            'last_action': self.last_action,
            'last_quantity': self.last_quantity,
            'current_price': self.current_price
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