# src/strategies/hedging_strategy/step19_cleanup_on_shutdown.py

from typing import Dict, Any
from src.utils.logger import get_logger

logger = get_logger(__name__)


class CleanupOnShutdown:
    """Полная очистка ресурсов при выключении стратегии"""

    def __init__(self, exchange, price_stream):
        self.exchange = exchange
        self.price_stream = price_stream
        self.symbol = exchange.symbol
        self.logger = get_logger(__name__)

    async def execute(self) -> Dict[str, Any]:
        """
        Полная очистка ресурсов при завершении работы стратегии

        Процесс:
        1. Остановить все watch'и в price_stream
        2. Отменить все стопы на бирже
        3. Закрыть WebSocket поток
        4. Закрыть Binance клиент

        Returns:
            {
                'success': bool,
                'stop_watches_success': bool,
                'cancel_stops_success': bool,
                'close_ws_success': bool,
                'close_client_success': bool,
                'error': str | None
            }
        """
        try:
            self.logger.info("Начинается полная очистка ресурсов hedging стратегии...")

            self.logger.info("1. Останавливаем все watch'и на цены")
            stop_watches_success = True
            try:
                self.price_stream.cancel_all_watches()
                self.logger.info("Все watch'и остановлены")
            except Exception as e:
                self.logger.error(f"Ошибка при остановке watch'ей: {e}")
                stop_watches_success = False

            self.logger.info("2. Отменяем все STOP_MARKET ордера на бирже")
            cancel_stops_success = True
            try:
                result = await self.exchange.cancel_all_stop_losses(self.symbol)
                if result:
                    self.logger.info("Все стоп-лосс ордера отменены")
                else:
                    self.logger.warning("Ошибка отмены всех стоп-лосс ордеров")
                    cancel_stops_success = False
            except Exception as e:
                self.logger.error(f"Ошибка при отмене стопов: {e}")
                cancel_stops_success = False

            self.logger.info("3. Закрываем WebSocket поток цен")
            close_ws_success = True
            try:
                self.price_stream.stop()
                self.logger.info("WebSocket поток закрыт")
            except Exception as e:
                self.logger.error(f"Ошибка при закрытии WebSocket: {e}")
                close_ws_success = False

            self.logger.info("4. Закрываем Binance клиент")
            close_client_success = True
            try:
                await self.exchange.close()
                self.logger.info("Binance клиент закрыт")
            except Exception as e:
                self.logger.error(f"Ошибка при закрытии Binance клиента: {e}")
                close_client_success = False

            self.logger.info("Очистка ресурсов hedging стратегии завершена")

            return {
                'success': all([
                    stop_watches_success,
                    cancel_stops_success,
                    close_ws_success,
                    close_client_success
                ]),
                'stop_watches_success': stop_watches_success,
                'cancel_stops_success': cancel_stops_success,
                'close_ws_success': close_ws_success,
                'close_client_success': close_client_success,
                'error': None
            }

        except Exception as e:
            self.logger.error(f"Критическая ошибка при очистке ресурсов: {e}")
            return {
                'success': False,
                'stop_watches_success': False,
                'cancel_stops_success': False,
                'close_ws_success': False,
                'close_client_success': False,
                'error': str(e)
            }