# src/strategies/hedging_strategy.py

import asyncio
from typing import Optional, Literal
from src.utils.logger import get_logger
from src.config.manager import config_manager
from src.binance.api import BinanceClient
from src.binance.hedge import HedgeManager
from src.binance.wss import BinancePriceStream

logger = get_logger(__name__)

Action = Literal["buy", "sell"]


class HedgingStrategy:
    """Торговая стратегия с хеджированием"""

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

        self.hedge_manager = HedgeManager(self.exchange)

        self.last_action: Optional[Action] = None
        self.main_twx: Optional[float] = None
        self.current_price: Optional[float] = None
        self._price_lock = asyncio.Lock()

        self.price_stream: Optional[BinancePriceStream] = None
        self._initialized = False

    async def initialize(self):
        """Async инициализация стратегии"""
        if self._initialized:
            return

        await self.exchange.initialize()

        hedge_mode_enabled = await self.exchange.enable_hedge_mode()
        if not hedge_mode_enabled:
            logger.error("Не удалось включить режим хеджирования")
            raise RuntimeError("Hedge mode не включен")

        self.price_stream = BinancePriceStream(self.symbol, self._on_price_update)
        self.price_stream.start()

        await self._restore_state_after_restart()

        self._initialized = True
        logger.info("Стратегия с хеджированием инициализирована")

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

            await self._process_price_update(price)
        except Exception as e:
            logger.error(f"Ошибка обработки обновления цены {price}: {e}")

    async def _process_price_update(self, current_price: float):
        """
        Обрабатывает обновление цены для мониторинга хеджирования

        Args:
            current_price: Текущая цена
        """
        if not self.hedge_manager.monitoring_active:
            return

        await self.hedge_manager.check_price_and_activate(current_price)

        if self.hedge_manager.active_hedge:
            await self.hedge_manager.check_hedge_pnl(current_price)
            hedge_closed = await self.hedge_manager.check_hedge_closed()

            if hedge_closed:
                await self._process_hedge_closed()

        elif self.hedge_manager.pending_hedge:
            if self.hedge_manager.check_main_pnl_crossed_activation(current_price):
                self.hedge_manager.hedge_can_be_triggered_again = True

    async def _process_hedge_closed(self):
        """Обрабатывает закрытие хеджа"""
        if not self.hedge_manager.hedging_enabled:
            logger.warning("Хеджирование отключено после неудач")

    async def get_current_price(self) -> Optional[float]:
        """
        Возвращает текущую цену из WebSocket потока с fallback

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

        Проверяет есть ли открытая позиция и загружает её состояние
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
            self.main_twx = current_position['entry_price']
            self.hedge_manager.hedging_enabled = False

            logger.info(f"Состояние восстановлено. Хеджирование отключено до следующего сигнала")

        except Exception as e:
            logger.error(f"Ошибка восстановления состояния после перезапуска: {e}")

    @staticmethod
    def parse_message(message: str) -> Optional[Action]:
        """
        Парсит сообщение от TradingView в торговый сигнал

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
        - Одинаковые сигналы подряд игнорируем
        - Противоположные сигналы обрабатываем
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
        action = HedgingStrategy.parse_message(message)
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
        1. Проверяем текущую позицию
        2. Если нет позиции - открываем новую
        3. Если есть позиция в том же направлении - игнорируем
        4. Если есть позиция в противоположном направлении - сценарий смены сигнала
        """
        try:
            normalized_symbol = self.exchange.normalize_symbol(self.symbol)
            current_position = await self.exchange.get_current_position(normalized_symbol)
            position_size = self._get_position_size()

            logger.info(f"Начинаем обработку сигнала {action} для {self.symbol}")

            if current_position is None:
                success = await self._handle_no_position(action, normalized_symbol, position_size)
            else:
                success = await self._handle_existing_position(action, current_position, normalized_symbol)

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
        current_price = await self.get_current_price()
        if current_price is None:
            logger.error("Цена недоступна")
            return False

        self.main_twx = current_price
        self.hedge_manager.reset_for_new_signal()

        quantity = self.exchange.calculate_quantity(normalized_symbol, position_size, current_price)
        logger.info(f"Расчет количества для {normalized_symbol}: {quantity}")

        if action == "buy":
            success = await self.exchange.open_long_position(normalized_symbol, quantity, position_side='LONG')
            position_side = 'Buy'
        else:
            success = await self.exchange.open_short_position(normalized_symbol, quantity, position_side='SHORT')
            position_side = 'Sell'

        if not success:
            logger.error(f"Не удалось открыть позицию {action}")
            return False

        entry_price = await self.exchange.get_exact_entry_price(normalized_symbol)
        if entry_price:
            self.main_twx = entry_price
            logger.info(f"Позиция открыта, точная цена входа ${entry_price:.2f}")

        self.hedge_manager.start_monitoring(normalized_symbol, self.main_twx, position_side)
        return True

    async def _handle_existing_position(self, action: Action, current_position: dict,
                                        normalized_symbol: str) -> bool:
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

        return await self._handle_signal_change(action, current_position, normalized_symbol)

    async def _handle_signal_change(self, action: Action, current_position: dict, normalized_symbol: str) -> bool:
        """
        Обрабатывает смену сигнала: закрыть старую позицию, открыть новую

        Args:
            action: Новый торговый сигнал
            current_position: Текущая позиция
            normalized_symbol: Нормализованный символ

        Returns:
            True если успешно
        """
        logger.info(f"Смена сигнала: текущая {current_position['side']}, новая {action}")

        try:
            current_price = await self.get_current_price()
            if current_price is None:
                logger.error("Цена недоступна для смены сигнала")
                return False

            self.hedge_manager.stop_monitoring()

            if self.hedge_manager.active_hedge:
                hedge = self.hedge_manager.active_hedge
                await self.exchange.cancel_all_hedge_stops(
                    symbol=normalized_symbol,
                    position_side=hedge.hedge_position_side
                )
                logger.info(f"Отменены все стопы хеджа {hedge.hedge_position_side}")

            position_size = self._get_position_size()

            position_side_to_close = 'LONG' if current_position['side'] == "Buy" else 'SHORT'
            opposite_side = "SELL" if current_position['side'] == "Buy" else "BUY"
            rounded_size = self.exchange.round_quantity(current_position['size'], normalized_symbol)

            logger.info(f"Закрываем {position_side_to_close} позицию через {opposite_side} ордер")

            await self.exchange.client.futures_create_order(
                symbol=normalized_symbol,
                side=opposite_side,
                type='MARKET',
                quantity=rounded_size,
                positionSide=position_side_to_close,
                reduceOnly=True
            )

            logger.info(f"Закрыта {current_position['side']} позиция")

            await asyncio.sleep(0.2)

            new_quantity = self.exchange.calculate_quantity(normalized_symbol, position_size, current_price)
            new_position_side = 'SHORT' if action == "sell" else 'LONG'
            new_side = 'SELL' if action == "sell" else 'BUY'

            logger.info(f"Открываем {new_position_side} позицию через {new_side} ордер, объем {new_quantity}")

            await self.exchange.client.futures_create_order(
                symbol=normalized_symbol,
                side=new_side,
                type='MARKET',
                quantity=new_quantity,
                positionSide=new_position_side
            )

            position_side_str = 'Buy' if action == "buy" else 'Sell'
            logger.info(f"Открыта новая {position_side_str} позиция по {current_price:.2f}")

            self.main_twx = current_price
            self.hedge_manager.reset_for_new_signal()
            self.hedge_manager.hedging_enabled = True

            self.hedge_manager.start_monitoring(normalized_symbol, self.main_twx, position_side_str)

            return True

        except Exception as e:
            logger.error(f"Ошибка при смене сигнала: {e}")
            return False

    @staticmethod
    def _get_position_size() -> float:
        """Получает размер позиции из конфигурации"""
        try:
            binance_config = config_manager.get_binance_config()
            position_size = binance_config.get('position_size')
            if position_size is None:
                raise ValueError("position_size не найден")

            return float(position_size)
        except Exception as e:
            logger.error(f"Ошибка получения размера позиции: {e}")
            raise RuntimeError(f"Не удалось загрузить размер позиции: {e}")

    async def cleanup(self):
        """Очистка ресурсов при завершении стратегии"""
        try:
            logger.info("Начинается очистка ресурсов стратегии с хеджированием...")

            if self.price_stream:
                self.price_stream.stop()

            if self.hedge_manager:
                self.hedge_manager.stop_monitoring()

            if self.exchange:
                await self.exchange.close()

            logger.info("Очистка ресурсов стратегии с хеджированием завершена")

        except Exception as e:
            logger.error(f"Ошибка при очистке ресурсов стратегии: {e}")

    def get_status(self) -> dict:
        """Возвращает текущий статус стратегии"""
        status = {
            'mode': 'hedging',
            'exchange': self.exchange.name,
            'symbol': self.symbol,
            'last_action': self.last_action,
            'main_twx': self.main_twx,
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

        if self.hedge_manager:
            status['hedge'] = self.hedge_manager.get_status()

        return status