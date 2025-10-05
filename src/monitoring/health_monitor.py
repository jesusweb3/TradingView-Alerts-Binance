# src/monitoring/health_monitor.py

import psutil
import time
import threading
from datetime import datetime
from typing import Optional, Dict, Any
from src.utils.logger import get_logger

logger = get_logger(__name__)


class HealthMonitor:
    """Внутренний монитор состояния сервера"""

    def __init__(self):
        self.last_request_time: Optional[datetime] = None
        self.last_health_check: Optional[datetime] = None
        self.last_self_test: Optional[datetime] = None
        self.start_time = datetime.now()
        self.request_count = 0
        self.health_check_count = 0
        self.self_test_failures = 0
        self.is_monitoring = False
        self.monitor_thread: Optional[threading.Thread] = None

        self.health_check_interval = 60
        self.self_test_interval = 300
        self.max_memory_mb = 500
        self.max_self_test_failures = 3

        self.consecutive_failures = 0
        self.max_consecutive_failures = 3

    def start_monitoring(self):
        """Запускает мониторинг в отдельном потоке"""
        if self.is_monitoring:
            return

        self.is_monitoring = True
        self.monitor_thread = threading.Thread(
            target=self._monitoring_loop,
            daemon=True,
            name="HealthMonitor"
        )
        self.monitor_thread.start()

    def stop_monitoring(self):
        """Останавливает мониторинг"""
        self.is_monitoring = False
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=5)
        logger.info("Мониторинг состояния остановлен")

    def record_request(self):
        """Записывает время последнего успешного запроса"""
        self.last_request_time = datetime.now()
        self.request_count += 1

        if self.consecutive_failures > 0:
            logger.info(f"Сервер восстановился после {self.consecutive_failures} проблем")
            self.consecutive_failures = 0

    def get_health_status(self) -> Dict[str, Any]:
        """Возвращает текущее состояние здоровья сервера"""
        now = datetime.now()
        uptime = now - self.start_time

        process = psutil.Process()
        memory_mb = process.memory_info().rss / 1024 / 1024

        time_since_last_self_test = None
        if self.last_self_test:
            time_since_last_self_test = (now - self.last_self_test).total_seconds()

        status = {
            "status": "healthy",
            "timestamp": now.isoformat(),
            "uptime_seconds": uptime.total_seconds(),
            "memory_mb": round(memory_mb, 2),
            "request_count": self.request_count,
            "health_check_count": self.health_check_count,
            "self_test_failures": self.self_test_failures,
            "last_request_time": self.last_request_time.isoformat() if self.last_request_time else None,
            "last_self_test": self.last_self_test.isoformat() if self.last_self_test else None,
            "time_since_last_self_test": time_since_last_self_test,
            "consecutive_failures": self.consecutive_failures
        }

        problems = []

        if memory_mb > self.max_memory_mb:
            problems.append(f"Высокое потребление памяти: {memory_mb:.1f}MB")

        if self.self_test_failures >= self.max_self_test_failures:
            problems.append(f"Множественные сбои self-test: {self.self_test_failures}")

        if self.consecutive_failures >= self.max_consecutive_failures:
            problems.append(f"Множественные сбои health-check: {self.consecutive_failures}")

        if problems:
            status["status"] = "unhealthy"
            status["problems"] = problems

        return status

    def _monitoring_loop(self):
        """Основной цикл мониторинга"""
        while self.is_monitoring:
            try:
                self._perform_health_check()
                time.sleep(self.health_check_interval)
            except Exception as e:
                logger.error(f"Ошибка в цикле мониторинга: {e}")
                time.sleep(10)

    def _perform_health_check(self):
        """Выполняет проверку здоровья"""
        self.last_health_check = datetime.now()
        self.health_check_count += 1

        try:
            if self._should_perform_self_test():
                self._perform_self_test()

            status = self.get_health_status()

            if status["status"] == "unhealthy":
                self.consecutive_failures += 1
                logger.warning(f"Обнаружены проблемы со здоровьем: {status['problems']}")
            else:
                if self.consecutive_failures > 0:
                    logger.info(f"Health check восстановлен после {self.consecutive_failures} сбоев")
                    self.consecutive_failures = 0

                if self.health_check_count % 10 == 0:
                    logger.info(f"Сервер здоров. Uptime: {status['uptime_seconds'] / 3600:.1f}ч")

        except Exception as e:
            self.consecutive_failures += 1
            logger.error(f"Ошибка при проверке здоровья: {e}")

    def _should_perform_self_test(self) -> bool:
        """Определяет нужно ли выполнять self-test"""
        if not self.last_self_test:
            return True

        time_since_last_test = (datetime.now() - self.last_self_test).total_seconds()
        return time_since_last_test >= self.self_test_interval

    def _perform_self_test(self):
        """Выполняет self-test сервера - проверяет что сервер может отвечать на запросы"""
        import requests
        import socket

        self.last_self_test = datetime.now()

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            result = sock.connect_ex(('127.0.0.1', 80))
            sock.close()

            if result != 0:
                raise ConnectionError("Сервер не отвечает на локальном порту")

            response = requests.get('http://127.0.0.1:80/health', timeout=10)

            if response.status_code != 200:
                raise ConnectionError(f"Health endpoint вернул код {response.status_code}")

            data = response.json()
            if data.get('status') != 'ok':
                raise ConnectionError("Health endpoint вернул неверные данные")

            if self.self_test_failures > 0:
                logger.info(f"Self-test восстановлен после {self.self_test_failures} сбоев")
                self.self_test_failures = 0

            logger.debug("Self-test успешен")

        except Exception as e:
            self.self_test_failures += 1
            logger.error(f"Self-test #{self.self_test_failures} неудачен: {e}")

            if self.self_test_failures >= self.max_self_test_failures:
                logger.critical(f"Self-test сбоит {self.self_test_failures} раз подряд - сервер не отвечает!")

    def force_health_check(self) -> Dict[str, Any]:
        """Принудительная проверка здоровья (для endpoint)"""
        status = self.get_health_status()
        logger.info(f"Принудительная проверка здоровья: {status['status']}")
        return status


health_monitor = HealthMonitor()