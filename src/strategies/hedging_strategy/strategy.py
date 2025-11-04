# src/strategies/hedging_strategy/strategy.py

import asyncio
from typing import Optional, Literal
from src.utils.logger import get_logger
from src.config.manager import config_manager
from src.binance.hedging_client import HedgingBinanceClient
from src.binance.wss import BinancePriceStream

from src.strategies.hedging_strategy.step1_restore_state_on_startup import RestoreStateOnStartup
from src.strategies.hedging_strategy.step2_ensure_hedge_mode import EnsureHedgeMode
from src.strategies.hedging_strategy.step3_parse_signal import ParseSignal
from src.strategies.hedging_strategy.step4_filter_duplicate_signal import FilterDuplicateSignal
from src.strategies.hedging_strategy.step5_handle_incoming_signal import HandleIncomingSignal
from src.strategies.hedging_strategy.step7_open_new_main_position import OpenNewMainPosition
from src.strategies.hedging_strategy.step8_close_and_reverse_main import CloseAndReverseMain
from src.strategies.hedging_strategy.step9_convert_hedge_to_main import ConvertHedgeToMain
from src.strategies.hedging_strategy.step10_start_activation_tracking import StartActivationTracking
from src.strategies.hedging_strategy.step11_on_activation_reached import OnActivationReached
from src.strategies.hedging_strategy.step12_setup_hedge_stops import SetupHedgeStops
from src.strategies.hedging_strategy.step13_place_initial_sl import PlaceInitialSL
from src.strategies.hedging_strategy.step14_start_dual_tracking import StartDualTracking
from src.strategies.hedging_strategy.step15_on_sl_hit import OnSLHit
from src.strategies.hedging_strategy.step16_on_trigger_hit import OnTriggerHit
from src.strategies.hedging_strategy.step17_on_tp_hit import OnTPHit
from src.strategies.hedging_strategy.step18_cancel_all_tracking import CancelAllTracking
from src.strategies.hedging_strategy.step19_cleanup_on_shutdown import CleanupOnShutdown

logger = get_logger(__name__)

Action = Literal["buy", "sell"]


class HedgingStrategy:
    """Полная hedging стратегия с управлением основной позицией и хеджем"""

    def __init__(self):
        binance_config = config_manager.get_binance_config()
        trading_config = config_manager.get_trading_config()
        hedging_config = config_manager.get_hedging_config()

        self.symbol = trading_config['symbol']

        self.exchange = HedgingBinanceClient(
            api_key=binance_config['api_key'],
            secret=binance_config['secret'],
            position_size=binance_config['position_size'],
            leverage=binance_config['leverage'],
            symbol=self.symbol
        )

        self.activation_pnl = hedging_config['activation_pnl']
        self.sl_pnl = hedging_config['sl_pnl']
        self.trigger_pnl = hedging_config['trigger_pnl']
        self.tp_pnl = hedging_config['tp_pnl']
        self.max_failures = hedging_config['max_failures']

        self.price_stream: Optional[BinancePriceStream] = None
        self._price_lock = asyncio.Lock()
        self.current_price: Optional[float] = None

        self.main_position_side: Optional[str] = None
        self.main_entry_price: Optional[float] = None
        self.previous_position_volume: Optional[float] = None
        self.hedge_position_side: Optional[str] = None
        self.hedge_entry_price: Optional[float] = None

        self.active_stop_order_id: Optional[str] = None
        self.failure_count: int = 0
        self.last_action: Optional[Action] = None

        self.tp_price_for_barrier: Optional[float] = None
        self.barrier_side: Optional[str] = None

        self._initialized = False

    async def initialize(self):
        """Async инициализация стратегии"""
        if self._initialized:
            return

        await self.exchange.initialize()

        self.price_stream = BinancePriceStream(self.symbol, self._on_price_update)
        self.price_stream.start()

        await self._restore_and_prepare()

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

    async def _restore_and_prepare(self):
        """ФАЗА 1: Восстановление состояния и подготовка"""
        try:
            logger.info("=== ФАЗА 1: ИНИЦИАЛИЗАЦИЯ & ВОССТАНОВЛЕНИЕ ===")

            step1 = RestoreStateOnStartup(self.exchange)
            restore_result = await step1.execute()

            self.main_position_side = restore_result.get('main_position_side')
            self.main_entry_price = restore_result.get('main_entry_price')
            self.previous_position_volume = restore_result.get('previous_position_volume')
            self.hedge_position_side = restore_result.get('hedge_position_side')
            self.hedge_entry_price = restore_result.get('hedge_entry_price')
            self.last_action = restore_result.get('last_action')

            positions_found = restore_result.get('positions_found', False)

            step2 = EnsureHedgeMode(self.exchange)
            hedge_mode_result = await step2.execute(positions_found)

            if not hedge_mode_result.get('success'):
                logger.warning("Не удалось переключиться в hedge mode, но продолжаем работу")

            logger.info("ФАЗА 1 завершена ✓")

        except Exception as e:
            logger.error(f"Ошибка инициализации: {e}")
            raise

    async def process_webhook(self, message: str) -> Optional[dict]:
        """Обрабатывает сообщение от webhook"""
        action_result = ParseSignal.execute(message)
        if not action_result.get('parsed'):
            logger.info("Сигнал не распарсен")
            return None

        action_value = action_result.get('action')

        filter_result = FilterDuplicateSignal.execute(action_value, self.last_action)
        if not filter_result.get('should_process'):
            return {"status": "ignored", "message": "Сигнал отфильтрован как дубликат"}

        self.last_action = filter_result.get('new_last_action')

        success = await self.process_signal(action_value)

        if success:
            return {
                "status": "success",
                "signal": {
                    "symbol": self.symbol,
                    "action": action_value
                }
            }
        else:
            return {"status": "error", "message": "Ошибка обработки сигнала"}

    async def process_signal(self, action: Action) -> bool:
        """Обрабатывает торговый сигнал через всю state-машину"""
        try:
            logger.info(f"=== ФАЗА 2-3: ПОЛУЧЕНИЕ И ОБРАБОТКА СИГНАЛА {action.upper()} ===")

            logger.info("Определяем сценарий обработки сигнала...")

            main_position_open = self.main_position_side is not None
            hedge_position_open = self.hedge_position_side is not None

            handle_result = HandleIncomingSignal.execute(
                action,
                main_position_open,
                hedge_position_open
            )

            scenario = handle_result.get('scenario')
            next_step = handle_result.get('next_step')

            logger.info(f"Выбран сценарий: {scenario}")
            logger.info(f"Следующий шаг: {next_step}")

            if scenario == 'no_position':
                await self._open_new_main_position(action)

            elif scenario == 'main_only':
                await self._close_and_reverse_main(action)

            elif scenario == 'main_and_hedge':
                await self._convert_hedge_to_main()

            else:
                logger.error(f"Неизвестный сценарий: {scenario}")
                return False

            return True

        except Exception as e:
            logger.error(f"Ошибка обработки сигнала {action}: {e}")
            return False

    async def _open_new_main_position(self, action: Action):
        """Открытие новой основной позиции (сценарий Б)"""
        try:
            logger.info(f"Открываем новую основную позицию {action.upper()}...")

            current_price = await self.get_current_price()

            if current_price is None:
                logger.error("Цена недоступна, не можем открыть позицию")
                return

            step7 = OpenNewMainPosition(self.exchange)
            open_result = await step7.execute(action, current_price)

            if open_result.get('success'):
                self.main_position_side = open_result.get('main_position_side')
                self.main_entry_price = open_result.get('main_entry_price')
                self.previous_position_volume = open_result.get('quantity')
                self.hedge_position_side = None
                self.hedge_entry_price = None
                self.active_stop_order_id = None
                self.failure_count = 0
                self.tp_price_for_barrier = None
                self.barrier_side = None

                logger.info(
                    f"Основная позиция открыта: {self.main_position_side} "
                    f"@ ${self.main_entry_price:.2f} ✓"
                )

                await self._start_activation_tracking()
            else:
                logger.error("Не удалось открыть основную позицию")

        except Exception as e:
            logger.error(f"Ошибка открытия основной позиции: {e}")

    async def _close_and_reverse_main(self, action: Action):
        """Закрытие текущей основной + открытие новой (сценарий В)"""
        try:
            logger.info("Закрываем текущую основную и открываем новую в противоположном направлении...")

            current_price = await self.get_current_price()

            if current_price is None:
                logger.error("Цена недоступна, не можем развернуть позицию")
                return

            step8 = CloseAndReverseMain(self.exchange)
            reverse_result = await step8.execute(action, current_price)

            if reverse_result.get('success'):
                self.main_position_side = reverse_result.get('new_position_side')
                self.main_entry_price = reverse_result.get('new_entry_price')
                self.previous_position_volume = reverse_result.get('new_quantity')
                self.hedge_position_side = None
                self.hedge_entry_price = None
                self.active_stop_order_id = None
                self.failure_count = 0
                self.tp_price_for_barrier = None
                self.barrier_side = None

                logger.info(
                    f"Разворот завершён: новая основная {self.main_position_side} "
                    f"@ ${self.main_entry_price:.2f} ✓"
                )

                await self._start_activation_tracking()
            else:
                logger.error("Не удалось развернуть позицию")

        except Exception as e:
            logger.error(f"Ошибка разворота позиции: {e}")

    async def _convert_hedge_to_main(self):
        """Конвертация хеджа в основную при новом сигнале (сценарий Г)"""
        try:
            logger.info("Конвертируем хедж в основную позицию...")

            current_price = await self.get_current_price()

            if current_price is None:
                logger.error("Цена недоступна для конвертации")
                return

            step9 = ConvertHedgeToMain(self.exchange, self.price_stream)
            convert_result = await step9.execute(
                current_price=current_price,
                hedge_entry_price=self.hedge_entry_price,
                hedge_position_side=self.hedge_position_side,
                active_stop_order_id=self.active_stop_order_id
            )

            if convert_result.get('success'):
                self.main_position_side = convert_result.get('new_main_position_side')
                self.main_entry_price = convert_result.get('new_main_entry_price')
                self.previous_position_volume = self.previous_position_volume
                self.hedge_position_side = None
                self.hedge_entry_price = None
                self.active_stop_order_id = None
                self.failure_count = 0
                self.tp_price_for_barrier = None
                self.barrier_side = None

                logger.info(
                    f"Конвертация завершена: хедж {convert_result.get('new_main_position_side')} "
                    f"→ основная позиция ✓"
                )

                await self._start_activation_tracking()
            else:
                logger.error("Не удалось конвертировать хедж в основную")

        except Exception as e:
            logger.error(f"Ошибка конвертации хеджа: {e}")

    async def _start_activation_tracking(self):
        """ФАЗА 4: Запуск отслеживания цены активации для открытия хеджа"""
        try:
            logger.info("=== ФАЗА 4: ОТСЛЕЖИВАНИЕ АКТИВАЦИИ ХЕДЖА ===")

            step10 = StartActivationTracking(self.exchange, self.price_stream)

            on_activation = self._create_on_activation_callback()

            activation_result = await step10.execute(
                main_position_side=self.main_position_side,
                main_entry_price=self.main_entry_price,
                activation_pnl=self.activation_pnl,
                on_activation_callback=on_activation,
                barrier_price=self.tp_price_for_barrier,
                barrier_side=self.barrier_side
            )

            if activation_result.get('success'):
                logger.info(f"Отслеживание активации запущено ✓")
            else:
                logger.error("Не удалось запустить отслеживание активации")

        except Exception as e:
            logger.error(f"Ошибка запуска отслеживания активации: {e}")

    def _create_on_activation_callback(self):
        """Создаёт async callback для срабатывания activation_price"""
        async def on_activation(current_price: float):
            await self._on_activation_reached(current_price)
        return on_activation

    async def _on_activation_reached(self, current_price: float):
        """ФАЗА 5: Срабатывание цены активации — открытие хеджа"""
        try:
            logger.info("=== ФАЗА 5: ОТКРЫТИЕ ХЕДЖА ===")

            step11 = OnActivationReached(self.exchange, self.price_stream)

            activation_price = self.exchange.calculate_activation_price(
                self.main_entry_price,
                self.main_position_side,
                self.activation_pnl
            )

            activation_callback_result = await step11.execute(
                current_price=current_price,
                main_position_side=self.main_position_side,
                activation_price=activation_price
            )

            if not activation_callback_result.get('success'):
                logger.error("Не удалось открыть хедж позицию")
                return

            self.hedge_position_side = activation_callback_result.get('hedge_position_side')
            self.hedge_entry_price = activation_callback_result.get('hedge_entry_price')

            logger.info(f"Хедж позиция открыта: {self.hedge_position_side} @ ${self.hedge_entry_price:.2f} ✓")

            await self._setup_and_place_hedge_stops()

        except Exception as e:
            logger.error(f"Ошибка открытия хеджа: {e}")

    async def _setup_and_place_hedge_stops(self):
        """ФАЗА 6: Расчёт и выставление стопов для хеджа"""
        try:
            logger.info("=== ФАЗА 6: ВЫСТАВЛЕНИЕ СТОПОВ ===")

            step12 = SetupHedgeStops(self.exchange)

            stops_result = step12.execute(
                hedge_entry_price=self.hedge_entry_price,
                hedge_position_side=self.hedge_position_side,
                sl_pnl=self.sl_pnl,
                trigger_pnl=self.trigger_pnl
            )

            if not stops_result.get('success'):
                logger.error("Не удалось рассчитать уровни стопов")
                return

            sl_price = stops_result.get('sl_price')
            trigger_price = stops_result.get('trigger_price')

            logger.info(f"SL уровень: ${sl_price:.2f}")
            logger.info(f"TRIGGER уровень: ${trigger_price:.2f}")

            step13 = PlaceInitialSL(self.exchange)

            sl_place_result = await step13.execute(
                hedge_position_side=self.hedge_position_side,
                sl_price=sl_price
            )

            if not sl_place_result.get('success'):
                logger.error("Не удалось выставить SL ордер")
                return

            self.active_stop_order_id = sl_place_result.get('active_stop_order_id')

            logger.info(f"SL ордер выставлен: ID={self.active_stop_order_id} ✓")

            await self._start_dual_tracking(sl_price, trigger_price)

        except Exception as e:
            logger.error(f"Ошибка выставления стопов: {e}")

    async def _start_dual_tracking(self, sl_price: float, trigger_price: float):
        """Запуск двойного отслеживания SL и TRIGGER"""
        try:
            logger.info("=== ФАЗА 7: ОТСЛЕЖИВАНИЕ SL И TRIGGER ===")

            step14 = StartDualTracking(self.price_stream)

            on_sl = self._create_on_sl_callback()
            on_trigger = self._create_on_trigger_callback()

            dual_result = await step14.execute(
                sl_price=sl_price,
                trigger_price=trigger_price,
                hedge_position_side=self.hedge_position_side,
                on_sl_callback=on_sl,
                on_trigger_callback=on_trigger
            )

            if dual_result.get('success'):
                logger.info("Двойное отслеживание запущено ✓")
            else:
                logger.error("Не удалось запустить двойное отслеживание")

        except Exception as e:
            logger.error(f"Ошибка запуска двойного отслеживания: {e}")

    def _create_on_sl_callback(self):
        """Создаёт callback для SL срабатывания"""
        async def on_sl(current_price: float):
            await self._on_sl_hit(current_price)
        return on_sl

    def _create_on_trigger_callback(self):
        """Создаёт callback для TRIGGER срабатывания"""
        async def on_trigger(current_price: float):
            await self._on_trigger_hit(current_price)
        return on_trigger

    async def _on_sl_hit(self, current_price: float):
        """ФАЗА 7A: SL срабатывает первым"""
        try:
            logger.info("🔴 SL СРАБАТИЛ ПЕРВЫМ")

            step15 = OnSLHit(self.price_stream)

            sl_result = await step15.execute(
                current_price=current_price,
                sl_price=current_price,
                failure_count=self.failure_count,
                max_failures=self.max_failures
            )

            if not sl_result.get('success'):
                logger.error("Ошибка обработки SL срабатывания")
                return

            self.failure_count = sl_result.get('failure_count')

            if sl_result.get('cycle_stopped'):
                logger.error(
                    f"Цикл хеджирования ОСТАНОВЛЕН (достигнут лимит неудачных хеджей {self.max_failures})"
                )
                self.hedge_entry_price = None
                self.active_stop_order_id = None
                return

            if sl_result.get('should_restart'):
                logger.info("Перезагружаем цикл отслеживания activation_price")
                self.hedge_entry_price = None
                self.active_stop_order_id = None
                await self._start_activation_tracking()

        except Exception as e:
            logger.error(f"Ошибка обработки SL срабатывания: {e}")

    async def _on_trigger_hit(self, current_price: float):
        """ФАЗА 7B: TRIGGER срабатывает первым"""
        try:
            logger.info("🟢 TRIGGER СРАБАТИЛ ПЕРВЫМ")

            step16 = OnTriggerHit(self.exchange, self.price_stream)

            trigger_result = await step16.execute(
                current_price=current_price,
                trigger_price=current_price,
                hedge_entry_price=self.hedge_entry_price,
                hedge_position_side=self.hedge_position_side,
                active_stop_order_id=self.active_stop_order_id,
                tp_pnl=self.tp_pnl,
                on_tp_callback=self._create_on_tp_callback()
            )

            if not trigger_result.get('success'):
                logger.error("Не удалось переместить стоп на TP")
                return

            self.active_stop_order_id = trigger_result.get('new_stop_order_id')
            tp_price = trigger_result.get('tp_price')

            logger.info(f"Новый TP SL выставлен на ${tp_price:.2f} ✓")

        except Exception as e:
            logger.error(f"Ошибка обработки TRIGGER срабатывания: {e}")

    def _create_on_tp_callback(self):
        """Создаёт callback для TP срабатывания"""
        async def on_tp(current_price: float):
            await self._on_tp_hit(current_price)
        return on_tp

    async def _on_tp_hit(self, current_price: float):
        """ФАЗА 7C: TP срабатывает — хедж закрыт в профит"""
        try:
            logger.info("✅ TP СРАБАТИЛ — ХЕДЖ ЗАКРЫТ В ПРОФИТ")

            tp_price = self.exchange.calculate_new_stop_price(
                self.hedge_entry_price,
                self.hedge_position_side,
                self.tp_pnl
            )

            step17 = OnTPHit(self.price_stream)

            tp_result = await step17.execute(
                current_price=current_price,
                tp_price=tp_price,
                main_position_side=self.main_position_side,
                failure_count=self.failure_count
            )

            if tp_result.get('success'):
                self.hedge_entry_price = None
                self.active_stop_order_id = None
                self.tp_price_for_barrier = tp_result.get('tp_price_for_barrier')
                self.barrier_side = tp_result.get('barrier_side')

                logger.info("Сохранены barrier-параметры для следующего цикла ✓")

                logger.info("Перезагружаем цикл отслеживания activation_price с barrier-логикой")
                await self._start_activation_tracking()
            else:
                logger.error("Ошибка обработки TP срабатывания")

        except Exception as e:
            logger.error(f"Ошибка обработки TP: {e}")

    async def cleanup(self):
        """ФАЗА 8: Полная очистка при завершении стратегии"""
        try:
            logger.info("Начинается очистка ресурсов hedging стратегии...")

            step18 = CancelAllTracking(self.exchange, self.price_stream)
            cancel_result = await step18.execute(self.active_stop_order_id)

            if cancel_result.get('success'):
                logger.info("Все tracking'и отменены ✓")

            step19 = CleanupOnShutdown(self.exchange, self.price_stream)
            cleanup_result = await step19.execute()

            if cleanup_result.get('success'):
                logger.info("Полная очистка ресурсов завершена ✓")
            else:
                logger.warning("Ошибки при очистке ресурсов")

        except Exception as e:
            logger.error(f"Ошибка при очистке ресурсов: {e}")

    def get_status(self) -> dict:
        """Возвращает текущий статус стратегии"""
        status = {
            'mode': 'hedging',
            'exchange': self.exchange.name,
            'symbol': self.symbol,
            'last_action': self.last_action,
            'current_price': self.current_price,
            'main_position': {
                'side': self.main_position_side,
                'entry_price': self.main_entry_price,
                'volume': self.previous_position_volume
            },
            'hedge_position': {
                'side': self.hedge_position_side,
                'entry_price': self.hedge_entry_price
            },
            'failure_count': self.failure_count,
            'max_failures': self.max_failures,
            'active_stop_order_id': self.active_stop_order_id,
            'barrier_price': self.tp_price_for_barrier,
            'barrier_side': self.barrier_side
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