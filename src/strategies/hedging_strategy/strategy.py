# src/strategies/hedging_strategy/strategy.py

import asyncio
from typing import Optional, Literal
from src.utils.logger import get_logger
from src.config.manager import config_manager
from src.binance.hedging_client import HedgingBinanceClient
from src.binance.wss import BinancePriceStream

logger = get_logger(__name__)

Action = Literal["buy", "sell"]


class HedgingStrategy:
    """Торговая стратегия хеджирования с управлением основной и хедж позициями"""

    def __init__(self):
        binance_config = config_manager.get_binance_config()
        trading_config = config_manager.get_trading_config()
        self.hedging_config = config_manager.get_hedging_config()

        self.symbol = trading_config['symbol']

        self.exchange = HedgingBinanceClient(
            api_key=binance_config['api_key'],
            secret=binance_config['secret'],
            position_size=binance_config['position_size'],
            leverage=binance_config['leverage'],
            symbol=self.symbol
        )

        self.last_action: Optional[Action] = None
        self.current_price: Optional[float] = None
        self._price_lock = asyncio.Lock()

        self.price_stream: Optional[BinancePriceStream] = None
        self._initialized = False

        self.main_position_side: Optional[str] = None
        self.hedge_position_side: Optional[str] = None
        self.main_entry_price: Optional[float] = None
        self.hedge_entry_price: Optional[float] = None

        self.failure_count: int = 0
        self.active_stop_order_id: Optional[str] = None

    async def initialize(self):
        """Async инициализация стратегии"""
        if self._initialized:
            return

        await self.exchange.initialize()

        self.price_stream = BinancePriceStream(self.symbol, self._on_price_update)
        self.price_stream.start()

        await self._restore_state_after_restart()

        self._initialized = True
        logger.info("Hedging стратегия инициализирована")

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
            all_positions = await self.exchange.get_all_positions(self.symbol)

            if not all_positions:
                logger.info("Позиций не обнаружено при старте")
                return

            logger.info(f"Обнаружено {len(all_positions)} открытых позиций при старте")

            for pos in all_positions:
                logger.info(
                    f"  {pos['side']}: {pos['size']} по ${pos['entry_price']:.2f}, "
                    f"PnL ${pos['unrealized_pnl']:.2f}"
                )

            self.main_position_side = all_positions[0]['side']
            self.main_entry_price = all_positions[0]['entry_price']
            self.last_action = "buy" if self.main_position_side == "LONG" else "sell"

            if len(all_positions) > 1:
                self.hedge_position_side = all_positions[1]['side']
                self.hedge_entry_price = all_positions[1]['entry_price']
                logger.info(f"Восстановлена основная позиция {self.main_position_side} "
                            f"и хедж {self.hedge_position_side}")
            else:
                self.hedge_position_side = "SHORT" if self.main_position_side == "LONG" else "LONG"
                self.hedge_entry_price = None
                logger.info(f"Восстановлена только основная позиция {self.main_position_side}")

            self.failure_count = 0

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
        """Обрабатывает торговый сигнал"""
        try:
            all_positions = await self.exchange.get_all_positions(self.symbol)
            position_size = self._get_position_size()

            logger.info(f"Начинаем обработку сигнала {action} для {self.symbol}")

            action_side = "LONG" if action == "buy" else "SHORT"

            if not all_positions:
                success = await self._handle_no_position(action_side, position_size)
            else:
                success = await self._handle_existing_position(action_side, all_positions, position_size)

            return success

        except Exception as e:
            logger.error(f"Ошибка обработки сигнала {action}: {e}")
            return False

    async def _handle_no_position(self, action_side: str, position_size: float) -> bool:
        """Обрабатывает сигнал когда позиции нет"""
        return await self._open_new_main_position(action_side, position_size)

    async def _handle_existing_position(self, action_side: str, all_positions: list, position_size: float) -> bool:
        """Обрабатывает сигнал когда позиция уже открыта"""
        main_pos = all_positions[0]
        current_side = main_pos['side']

        if current_side == action_side:
            logger.info(f"Позиция {self.symbol} уже в направлении {action_side} - пропускаем")
            return True

        await self._cancel_all_watches_and_stops()

        if len(all_positions) == 1:
            await self._close_main_position(current_side, main_pos['size'])
        else:
            hedge_pos = all_positions[1]
            await self._close_main_position(current_side, main_pos['size'])

            self.main_position_side = hedge_pos['side']
            self.main_entry_price = self.current_price or self.price_stream.get_last_price()
            self.hedge_position_side = "SHORT" if self.main_position_side == "LONG" else "LONG"
            self.hedge_entry_price = None
            self.active_stop_order_id = None
            self.failure_count = 0

            logger.info(f"Хедж позиция {self.main_position_side} стала основной, "
                        f"виртуальная ТВХ = ${self.main_entry_price:.2f}")

            await self._start_hedging_cycle()
            return True

        return await self._open_new_main_position(action_side, position_size)

    async def _open_new_main_position(self, action_side: str, position_size: float) -> bool:
        """Открывает новую основную позицию"""
        current_price = await self.get_current_price()

        if current_price is None:
            current_price = self.price_stream.get_last_price()
            if current_price is None:
                logger.error("Цена из WebSocket недоступна")
                return False
            logger.warning(f"Используем последнюю известную цену ${current_price:.2f}")

        quantity = self.exchange.calculate_quantity(self.symbol, position_size, current_price)

        if action_side == "LONG":
            success = await self.exchange.open_long_position(self.symbol, quantity)
            direction = "LONG"
        else:
            success = await self.exchange.open_short_position(self.symbol, quantity)
            direction = "SHORT"

        if not success:
            logger.error(f"Не удалось открыть {direction} позицию {self.symbol}")
            return False

        self.main_position_side = action_side
        self.hedge_position_side = "SHORT" if action_side == "LONG" else "LONG"

        entry_price = await self.exchange.get_exact_entry_price(self.symbol, action_side)
        if entry_price:
            self.main_entry_price = entry_price
        else:
            self.main_entry_price = current_price

        self.hedge_entry_price = None
        self.failure_count = 0
        self.active_stop_order_id = None

        logger.info(f"Основная позиция {direction} открыта, ТВХ = ${self.main_entry_price:.2f}")

        await self._start_hedging_cycle()
        return True

    async def _close_main_position(self, position_side: str, quantity: float) -> bool:
        """Закрывает основную позицию"""
        try:
            success = await self.exchange.close_position(self.symbol, position_side, quantity)
            if success:
                logger.info(f"Основная позиция {position_side} закрыта")
            return success
        except Exception as e:
            logger.error(f"Ошибка закрытия основной позиции: {e}")
            return False

    async def _start_hedging_cycle(self):
        """Запускает цикл отслеживания активации хеджа"""
        try:
            if not self.main_entry_price:
                logger.error("main_entry_price не установлена")
                return

            activation_pnl = self.hedging_config['activation_pnl']

            activation_price = self.exchange.calculate_activation_price(
                self.main_entry_price,
                self.main_position_side,
                activation_pnl
            )

            # Для LONG: цена падает к activation_price (direction='short')
            # Для SHORT: цена растёт к activation_price (direction='long')
            direction = 'short' if self.main_position_side == 'LONG' else 'long'

            logger.info(
                f"Отслеживаем цену ${activation_price:.2f} (PNL {activation_pnl}%). "
                f"Если основная позиция достигнет этого уровня, откроем хедж ({self.hedge_position_side})"
            )

            callback = self._create_activation_callback()
            self.price_stream.watch_price(
                target_price=activation_price,
                direction=direction,
                on_reach=callback
            )

        except Exception as e:
            logger.error(f"Ошибка запуска цикла хеджирования: {e}")

    async def _on_activation_price_reached(self, current_price: float):
        """Колбек вызываемый когда цена достигает уровня активации хеджа"""
        try:
            logger.info(f"Цена активации достигнута (${current_price:.2f}), открываем хедж")
            await self._open_hedge_position(current_price)
        except Exception as e:
            logger.error(f"Ошибка при открытии хеджа: {e}")

    async def _open_hedge_position(self, current_price: float):
        """Открывает хедж позицию и выставляет стоп на основе реальной ТВХ"""
        try:
            if not self.hedge_position_side:
                logger.error("hedge_position_side не установлена")
                return

            position_size = self._get_position_size()
            quantity = self.exchange.calculate_quantity(self.symbol, position_size, current_price)

            # ШАГ 1: Открываем хедж
            if self.hedge_position_side == "LONG":
                success = await self.exchange.open_long_position(self.symbol, quantity)
            else:
                success = await self.exchange.open_short_position(self.symbol, quantity)

            if not success:
                logger.error(f"Не удалось открыть хедж позицию {self.hedge_position_side}")
                return

            # ШАГ 2: Получаем РЕАЛЬНЫЙ ТВХ от биржи
            entry_price = await self.exchange.get_exact_entry_price(self.symbol, self.hedge_position_side)

            if not entry_price:
                logger.error("Не удалось получить реальный ТВХ хеджа после открытия")
                return

            self.hedge_entry_price = entry_price
            logger.info(f"Хедж {self.hedge_position_side} открыта, реальный ТВХ = ${self.hedge_entry_price:.2f}")

            # ШАГ 3: На основе РЕАЛЬНОГО ТВХ рассчитываем и выставляем стоп
            await self._set_hedge_stop_loss()

        except Exception as e:
            logger.error(f"Ошибка открытия хеджа: {e}")

    async def _set_hedge_stop_loss(self):
        """Выставляет стоп-лосс для хеджа на основе РЕАЛЬНОГО ТВХ и запускает мониторинг"""
        try:
            if not self.hedge_entry_price or not self.hedge_position_side:
                logger.error("hedge_entry_price или hedge_position_side не установлены")
                return

            if self.hedge_entry_price <= 0:
                logger.error(f"Невалидный ТВХ хеджа: {self.hedge_entry_price}")
                return

            logger.info(f"Расчёт стопа на основе РЕАЛЬНОГО ТВХ: ${self.hedge_entry_price:.2f}")

            sl_pnl = self.hedging_config['sl_pnl']
            sl_price = self.exchange.calculate_stop_loss_price(
                self.hedge_entry_price,
                self.hedge_position_side,
                sl_pnl
            )

            result = await self.exchange.place_stop_loss_order(
                symbol=self.symbol,
                position_side=self.hedge_position_side,
                stop_price=sl_price
            )

            if result:
                self.active_stop_order_id = result.get('orderId')
                logger.info(
                    f"Стоп-лосс хеджа выставлен на основе реального ТВХ: ID={self.active_stop_order_id}, цена={sl_price}")
            else:
                logger.error("Не удалось выставить стоп-лосс хеджа")
                return

            trigger_pnl = self.hedging_config['trigger_pnl']
            trigger_price = self.exchange.calculate_trigger_price(
                self.hedge_entry_price,
                self.hedge_position_side,
                trigger_pnl
            )

            # Определяем направления для двух отдельных сценариев
            if self.hedge_position_side == 'SHORT':
                sl_direction = 'long'
                sl_barrier_side = 'below'
                trigger_direction = 'short'
                trigger_barrier_side = 'above'
            else:  # LONG
                sl_direction = 'short'
                sl_barrier_side = 'above'
                trigger_direction = 'long'
                trigger_barrier_side = 'below'

            logger.info(
                f"Мониторим хедж позицию: "
                f"SL (убыток) = ${sl_price:.2f}, "
                f"ТРИГГЕР (профит) = ${trigger_price:.2f}"
            )

            # Watch 1: SL
            sl_callback = self._create_sl_callback()
            self.price_stream.watch_price(
                target_price=sl_price,
                direction=sl_direction,
                on_reach=sl_callback,
                barrier_price=self.main_entry_price,
                barrier_side=sl_barrier_side
            )

            # Watch 2: Триггер
            trigger_callback = self._create_trigger_callback()
            self.price_stream.watch_price(
                target_price=trigger_price,
                direction=trigger_direction,
                on_reach=trigger_callback,
                barrier_price=self.main_entry_price,
                barrier_side=trigger_barrier_side
            )

        except Exception as e:
            logger.error(f"Ошибка установки стопа хеджа: {e}")

    async def _on_sl_price_reached(self, current_price: float):
        """Колбек когда цена достигает SL (хедж закрывается в убыток)"""
        try:
            self.failure_count += 1
            logger.warning(
                f"SL хеджа достигнут (${current_price:.2f}). "
                f"Убыточных хеджей: {self.failure_count}/{self.hedging_config['max_failures']}"
            )

            # Отменяем все текущие watch'и
            self.price_stream.cancel_all_watches()

            if self.failure_count >= self.hedging_config['max_failures']:
                logger.error(f"Достигнут лимит убыточных хеджей ({self.failure_count}). Цикл остановлен.")
                self.active_stop_order_id = None
                self.hedge_entry_price = None
                return

            self.active_stop_order_id = None
            self.hedge_entry_price = None

            await self._restart_hedging_cycle()

        except Exception as e:
            logger.error(f"Ошибка обработки SL: {e}")

    async def _on_trigger_price_reached(self, current_price: float):
        """Колбек когда цена достигает триггера (пора переносить стоп в профит)"""
        try:
            logger.info(f"Триггер достигнут (${current_price:.2f}), переносим стоп в профит")

            # Отменяем все текущие watch'и (SL и триггер)
            self.price_stream.cancel_all_watches()

            tp_pnl = self.hedging_config['tp_pnl']
            tp_price = self.exchange.calculate_new_stop_price(
                self.hedge_entry_price,
                self.hedge_position_side,
                tp_pnl
            )

            success = await self.exchange.update_stop_loss_order(
                symbol=self.symbol,
                position_side=self.hedge_position_side,
                new_stop_price=tp_price
            )

            if success:
                logger.info(f"Стоп перенесён на TP цену ${tp_price:.2f} (в профит)")

                # Watch для нового SL (в профит)
                # Для SHORT: цена растёт СНИЗУ ВВЕРХ от триггера к TP
                # Для LONG: цена падает СВЕРХУ ВНИЗ от триггера к TP
                if self.hedge_position_side == 'SHORT':
                    tp_direction = 'long'
                    tp_barrier_side = 'below'
                else:  # LONG
                    tp_direction = 'short'
                    tp_barrier_side = 'above'

                tp_callback = self._create_tp_callback()
                self.price_stream.watch_price(
                    target_price=tp_price,
                    direction=tp_direction,
                    on_reach=tp_callback,
                    barrier_price=current_price,
                    barrier_side=tp_barrier_side
                )

            else:
                logger.error("Не удалось обновить стоп")

        except Exception as e:
            logger.error(f"Ошибка обработки триггера: {e}")

    async def _on_tp_price_reached(self, current_price: float):
        """Колбек когда цена достигает TP (хедж закрывается в профит)"""
        try:
            logger.info(f"TP достигнут (${current_price:.2f}), хедж закрывается в профит")

            # Отменяем все watch'и
            self.price_stream.cancel_all_watches()

            # Очищаем состояние хеджа
            self.active_stop_order_id = None
            self.hedge_entry_price = None

            # Перезапускаем цикл отслеживания активации
            await self._restart_hedging_cycle()

        except Exception as e:
            logger.error(f"Ошибка обработки TP: {e}")

    async def _restart_hedging_cycle(self):
        """Перезапускает цикл отслеживания активации хеджа"""
        try:
            activation_pnl = self.hedging_config['activation_pnl']
            activation_price = self.exchange.calculate_activation_price(
                self.main_entry_price,
                self.main_position_side,
                activation_pnl
            )

            # Для LONG: цена должна упасть (direction='short')
            # Для SHORT: цена должна вырасти (direction='long')
            direction = 'short' if self.main_position_side == 'LONG' else 'long'

            logger.info(
                f"Перезапускаем отслеживание. При PNL основной позиции {activation_pnl}% "
                f"(цена ${activation_price:.2f}) откроем новый хедж"
            )

            callback = self._create_activation_callback()
            self.price_stream.watch_price(
                target_price=activation_price,
                direction=direction,
                on_reach=callback
            )

        except Exception as e:
            logger.error(f"Ошибка перезапуска цикла: {e}")

    async def _cancel_all_watches_and_stops(self):
        """Отменяет все watch-цены и стоп-лосс ордера"""
        try:
            self.price_stream.cancel_all_watches()

            if self.active_stop_order_id:
                await self.exchange.cancel_all_stop_losses(self.symbol)
                self.active_stop_order_id = None

            logger.info("Все watch-цены и стопы отменены")

        except Exception as e:
            logger.error(f"Ошибка отмены watch-цен и стопов: {e}")

    def _create_activation_callback(self):
        """Создаёт async callback для активации хеджа"""

        async def callback(current_price: float) -> None:
            await self._on_activation_price_reached(current_price)

        return callback

    def _create_sl_callback(self):
        """Создаёт async callback для SL"""

        async def callback(current_price: float) -> None:
            await self._on_sl_price_reached(current_price)

        return callback

    def _create_trigger_callback(self):
        """Создаёт async callback для триггера"""

        async def callback(current_price: float) -> None:
            await self._on_trigger_price_reached(current_price)

        return callback

    def _create_tp_callback(self):
        """Создаёт async callback для TP"""

        async def callback(current_price: float) -> None:
            await self._on_tp_price_reached(current_price)

        return callback

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
            logger.info("Начинается очистка ресурсов hedging стратегии...")

            await self._cancel_all_watches_and_stops()

            if self.price_stream:
                self.price_stream.stop()

            if self.exchange:
                await self.exchange.close()

            logger.info("Очистка ресурсов hedging стратегии завершена")

        except Exception as e:
            logger.error(f"Ошибка при очистке ресурсов стратегии: {e}")

    def get_status(self) -> dict:
        """Возвращает текущий статус стратегии"""
        status = {
            'mode': 'hedging',
            'exchange': self.exchange.name,
            'symbol': self.symbol,
            'main_position_side': self.main_position_side,
            'main_entry_price': self.main_entry_price,
            'hedge_position_side': self.hedge_position_side,
            'hedge_entry_price': self.hedge_entry_price,
            'failure_count': self.failure_count,
            'max_failures': self.hedging_config['max_failures'],
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