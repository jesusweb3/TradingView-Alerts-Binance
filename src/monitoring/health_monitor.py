# src/monitoring/health_monitor.py

import time
import threading
from typing import Optional, TYPE_CHECKING
from src.utils.logger import get_logger

if TYPE_CHECKING:
    from src.strategies.strategy import Strategy

logger = get_logger(__name__)


class HealthMonitor:
    """Мониторинг состояния сервера через локальные запросы"""

    def __init__(self):
        self.is_monitoring = False
        self.monitor_thread: Optional[threading.Thread] = None
        self.strategy: Optional['Strategy'] = None
        self.monitoring_interval = 600
        self.initial_delay = 10

    def start_monitoring(self, strategy: 'Strategy'):
        """Запускает мониторинг в отдельном потоке"""
        if self.is_monitoring:
            return

        self.strategy = strategy
        self.is_monitoring = True
        self.monitor_thread = threading.Thread(
            target=self._monitoring_loop,
            daemon=True,
            name="HealthMonitor"
        )
        self.monitor_thread.start()
        logger.info("Мониторинг здоровья запущен (интервал 10 минут)")

    def stop_monitoring(self):
        """Останавливает мониторинг"""
        self.is_monitoring = False
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=5)
        logger.info("Мониторинг остановлен")

    def record_request(self):
        """Записывает успешный webhook запрос"""
        pass

    def _monitoring_loop(self):
        """Основной цикл мониторинга"""
        time.sleep(self.initial_delay)

        while self.is_monitoring:
            try:
                self._perform_health_check()
                time.sleep(self.monitoring_interval)
            except Exception as e:
                logger.error(f"Ошибка в цикле мониторинга: {e}")
                time.sleep(60)

    def _perform_health_check(self):
        """Выполняет проверку через локальный запрос к /health"""
        import requests

        try:
            response = requests.get('http://127.0.0.1:80/health', timeout=10)

            if response.status_code != 200:
                logger.error(f"Health endpoint вернул код {response.status_code}")
                return

            data = response.json()
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

            if is_healthy:
                if 'current_downtime_seconds' in stats:
                    downtime = stats['current_downtime_seconds']
                    status = f"переподключается (простой {downtime:.0f}s)"
                else:
                    status = "активен"
            else:
                if stats.get('last_successful_connection'):
                    status = "отключен >5 минут"
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