# src/monitoring/health_monitor.py

import psutil
import time
import threading
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from src.utils.logger import get_logger

logger = get_logger(__name__)


class HealthMonitor:
    """Внутренний монитор состояния сервера с автоперезапуском"""

    def __init__(self, restart_callback=None):
        self.restart_callback = restart_callback
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

        self.midnight_restart_enabled = True
        self.midnight_restart_done = False

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

        next_midnight = self._get_next_midnight()
        time_until_midnight = (next_midnight - now).total_seconds()

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
            "consecutive_failures": self.consecutive_failures,
            "next_midnight_restart": next_midnight.isoformat(),
            "time_until_midnight_restart": time_until_midnight
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

    @staticmethod
    def _get_next_midnight() -> datetime:
        """Возвращает время следующей полночи"""
        now = datetime.now()
        tomorrow = now.date() + timedelta(days=1)
        return datetime.combine(tomorrow, datetime.min.time())

    def _is_time_for_midnight_restart(self) -> bool:
        """Проверяет пришло ли время для перезапуска строго в полночь"""
        if not self.midnight_restart_enabled:
            return False

        if self.midnight_restart_done:
            return False

        now = datetime.now()

        if now.hour == 0 and now.minute == 0 and now.second < 60:
            uptime_hours = (now - self.start_time).total_seconds() / 3600
            if uptime_hours > 1:
                self.midnight_restart_done = True
                return True

        if now.hour != 0:
            self.midnight_restart_done = False

        return False

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
            if self._is_time_for_midnight_restart():
                self._trigger_midnight_restart()
                return

            if self._should_perform_self_test():
                self._perform_self_test()

            status = self.get_health_status()

            if status["status"] == "unhealthy":
                self.consecutive_failures += 1
                logger.warning(f"Обнаружены проблемы со здоровьем: {status['problems']}")

                if self._should_restart(status):
                    logger.critical("Инициируется перезапуск сервера")
                    self._trigger_restart(status)
            else:
                if self.consecutive_failures > 0:
                    logger.info(f"Health check восстановлен после {self.consecutive_failures} сбоев")
                    self.consecutive_failures = 0

                if self.health_check_count % 10 == 0:
                    next_midnight = self._get_next_midnight()
                    hours_until_midnight = (next_midnight - datetime.now()).total_seconds() / 3600

                    logger.info(f"Сервер здоров. Uptime: {status['uptime_seconds'] / 3600:.1f}ч, "
                                f"до перезапуска: {hours_until_midnight:.1f}ч")

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

    def _should_restart(self, status: Dict[str, Any]) -> bool:
        """Определяет нужен ли перезапуск"""
        critical_conditions = [
            self.consecutive_failures >= self.max_consecutive_failures,
            status.get("memory_mb", 0) > self.max_memory_mb * 2,
            self.self_test_failures >= self.max_self_test_failures,
        ]

        return any(critical_conditions)

    def _trigger_midnight_restart(self):
        """Запускает плановый перезапуск в полночь"""
        logger.info("=== ИНИЦИИРОВАН ПЛАНОВЫЙ ПЕРЕЗАПУСК ПРИЛОЖЕНИЯ ===")

        if self.restart_callback:
            try:
                self.is_monitoring = False
                self.restart_callback()
            except Exception as e:
                logger.error(f"Ошибка при выполнении перезапуска: {e}")
        else:
            logger.critical("Callback для перезапуска не установлен!")

    def _trigger_restart(self, status: Dict[str, Any]):
        """Запускает процедуру перезапуска"""
        logger.critical("=== ИНИЦИИРОВАН ПЕРЕЗАПУСК СЕРВЕРА ===")
        logger.critical(f"Причины: {status.get('problems', [])}")
        logger.critical(f"Статистика: Memory={status.get('memory_mb')}MB, "
                        f"Uptime={status.get('uptime_seconds', 0) / 3600:.1f}ч, "
                        f"Failures={self.consecutive_failures}")

        if self.restart_callback:
            try:
                self.is_monitoring = False
                self.restart_callback()
            except Exception as e:
                logger.error(f"Ошибка при выполнении перезапуска: {e}")
        else:
            logger.critical("Callback для перезапуска не установлен!")

    def force_health_check(self) -> Dict[str, Any]:
        """Принудительная проверка здоровья (для endpoint)"""
        status = self.get_health_status()
        logger.info(f"Принудительная проверка здоровья: {status['status']}")
        return status


health_monitor = HealthMonitor()