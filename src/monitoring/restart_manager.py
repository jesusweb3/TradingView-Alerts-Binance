# src/monitoring/restart_manager.py

import os
import sys
import time
import threading
from src.utils.logger import get_logger

logger = get_logger(__name__)

_restart_in_progress = False
_restart_lock = threading.Lock()


def request_restart():
    """Запрашивает перезапуск приложения"""
    global _restart_in_progress

    with _restart_lock:
        if _restart_in_progress:
            logger.warning("Перезапуск уже запрошен, игнорируем повторный запрос")
            return

        _restart_in_progress = True

    restart_thread = threading.Thread(
        target=_perform_restart,
        daemon=False,
        name="RestartThread"
    )
    restart_thread.start()


def _perform_restart():
    """Выполняет перезапуск приложения"""
    try:
        restart_delay = 3
        time.sleep(restart_delay)

        logger.info("Выполняется перезапуск приложения...")

        _graceful_shutdown()

        time.sleep(1)

        python_executable = sys.executable
        os.execv(python_executable, [python_executable] + sys.argv)

    except Exception as e:
        logger.error(f"Ошибка при перезапуске приложения: {e}")
        logger.critical("Перезапуск не удался, приложение может быть в нерабочем состоянии")
        logger.critical("Выполняется принудительный выход из приложения")
        os._exit(1)


def _graceful_shutdown():
    """Корректное завершение всех ресурсов перед перезапуском"""
    try:
        logger.info("Завершение активных соединений и ресурсов...")

        import asyncio
        try:
            loop = asyncio.get_running_loop()
            if loop and not loop.is_closed():
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                logger.debug(f"Отменено {len(pending)} asyncio задач")
        except RuntimeError:
            pass

        active_threads = threading.enumerate()
        daemon_threads = [t for t in active_threads if t.daemon and t.is_alive() and t != threading.current_thread()]
        logger.debug(f"Активных daemon потоков: {len(daemon_threads)}")

        time.sleep(1)

        logger.info("Graceful shutdown завершен")

    except Exception as e:
        logger.warning(f"Ошибка при graceful shutdown: {e}")