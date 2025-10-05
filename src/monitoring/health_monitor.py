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

            current_price = self._get_current_websocket_price()
            if current_price:
                logger.info(f"Health check OK | WebSocket цена: ${current_price:.2f}")
            else:
                logger.info("Health check OK | WebSocket цена: недоступна")

        except Exception as e:
            logger.error(f"Ошибка health check: {e}")

    def _get_current_websocket_price(self) -> Optional[float]:
        """Получает текущую цену из WebSocket через стратегию"""
        try:
            if self.strategy is None:
                return None

            return self.strategy.get_current_price()

        except Exception as e:
            logger.error(f"Ошибка получения цены из WebSocket: {e}")
            return None


health_monitor = HealthMonitor()