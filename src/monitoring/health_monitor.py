# src/monitoring/health_monitor.py

import asyncio
import aiohttp
from typing import Optional, TYPE_CHECKING, Union
from datetime import datetime, timezone
from src.utils.logger import get_logger

if TYPE_CHECKING:
    from src.strategies.classic_strategy import ClassicStrategy
    from src.strategies.stop_strategy import StopStrategy

logger = get_logger(__name__)


class HealthMonitor:
    """Async мониторинг состояния сервера"""

    def __init__(self):
        self.is_monitoring = False
        self.monitor_task: Optional[asyncio.Task] = None
        self.strategy: Optional[Union['ClassicStrategy', 'StopStrategy']] = None
        self.monitoring_interval = 600
        self.initial_delay = 10

    def start_monitoring(self, strategy: Union['ClassicStrategy', 'StopStrategy']):
        """Запускает мониторинг в async задаче"""
        if self.is_monitoring:
            return

        self.strategy = strategy
        self.is_monitoring = True
        self.monitor_task = asyncio.create_task(self._monitoring_loop())
        logger.info("Мониторинг здоровья запущен (интервал 10 минут)")

    async def stop_monitoring(self):
        """Останавливает мониторинг"""
        self.is_monitoring = False
        if self.monitor_task and not self.monitor_task.done():
            self.monitor_task.cancel()
            try:
                await self.monitor_task
            except asyncio.CancelledError:
                pass
        logger.info("Мониторинг остановлен")

    def record_request(self):
        """Записывает успешный webhook запрос"""
        pass

    async def _monitoring_loop(self):
        """Основной цикл мониторинга"""
        await asyncio.sleep(self.initial_delay)

        while self.is_monitoring:
            try:
                await self._perform_health_check()
                await asyncio.sleep(self.monitoring_interval)
            except asyncio.CancelledError:
                logger.info("Мониторинг отменён")
                break
            except Exception as e:
                logger.error(f"Ошибка в цикле мониторинга: {e}")
                await asyncio.sleep(60)

    async def _perform_health_check(self):
        """Выполняет проверку через локальный запрос к /health"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get('http://127.0.0.1:80/health', timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status != 200:
                        logger.error(f"Health endpoint вернул код {response.status}")
                        return

                    data = await response.json()
                    if data.get('status') != 'ok':
                        logger.error("Health endpoint вернул неверные данные")
                        return

                    ws_info = self._get_websocket_status()

                    if ws_info['is_healthy']:
                        logger.info(
                            f"Health check OK | WebSocket: {ws_info['status']} | "
                            f"Цена: ${ws_info['price']:.2f} | "
                            f"Переподключений: {ws_info['reconnections']}"
                        )
                    else:
                        logger.warning(
                            f"Health check: WebSocket проблемы | {ws_info['status']} | "
                            f"Последняя цена: ${ws_info['price']:.2f}"
                        )

        except asyncio.TimeoutError:
            logger.error("Health check: таймаут запроса")
        except aiohttp.ClientError as e:
            logger.error(f"Health check: ошибка соединения {e}")
        except Exception as e:
            logger.error(f"Ошибка health check: {e}")

    def _get_websocket_status(self) -> dict:
        """
        Получает статус WebSocket соединения

        Returns:
            Словарь со статусом WebSocket и последней ценой
        """
        default_response = {
            'is_healthy': False,
            'status': 'недоступен',
            'price': 0.0,
            'reconnections': 0
        }

        try:
            if self.strategy is None or self.strategy.price_stream is None:
                return default_response

            price_stream = self.strategy.price_stream

            is_healthy = price_stream.is_healthy()
            stats = price_stream.get_connection_stats()

            last_price = stats.get('last_price', 0.0)
            if last_price is None:
                last_price = 0.0

            connection_count = stats.get('connection_count', 0)
            is_connected = stats.get('is_connected', False)
            last_price_update = stats.get('last_price_update')

            if is_healthy:
                if last_price_update:
                    seconds_since_update = (
                            datetime.now(timezone.utc) - last_price_update
                    ).total_seconds()
                    status = f"активен (обновление {seconds_since_update:.0f}s назад)"
                else:
                    status = "активен"
            else:
                if is_connected:
                    status = "подключен, но нет данных"
                elif 'current_downtime_seconds' in stats:
                    downtime = stats['current_downtime_seconds']
                    status = f"переподключается (простой {downtime:.0f}s)"
                elif stats.get('last_successful_connection'):
                    status = "отключен >1 минуты"
                else:
                    status = "никогда не подключался"

            return {
                'is_healthy': is_healthy,
                'status': status,
                'price': last_price,
                'reconnections': connection_count - 1 if connection_count > 0 else 0
            }

        except Exception as e:
            logger.error(f"Ошибка получения статуса WebSocket: {e}")
            return default_response


health_monitor = HealthMonitor()