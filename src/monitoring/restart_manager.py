# src/monitoring/restart_manager.py

import os
import sys
import time
import threading
from src.utils.logger import get_logger

logger = get_logger(__name__)

_restart_in_progress = False
_restart_lock = threading.Lock()


def request_restart(reason: str = "Health check failure"):
    """Запрашивает перезапуск приложения"""
    global _restart_in_progress

    with _restart_lock:
        if _restart_in_progress:
            logger.warning("Перезапуск уже запрошен, игнорируем повторный запрос")
            return

        _restart_in_progress = True

    logger.critical(f"Запрошен перезапуск приложения. Причина: {reason}")

    restart_thread = threading.Thread(
        target=_perform_restart,
        args=(reason,),
        daemon=True,
        name="RestartThread"
    )
    restart_thread.start()


def _perform_restart(reason: str):
    """Выполняет перезапуск приложения"""
    try:
        restart_delay = 3
        logger.info(f"Начинается процедура перезапуска через {restart_delay} секунд...")
        logger.info(f"Причина перезапуска: {reason}")

        time.sleep(restart_delay)

        logger.info("Выполняется перезапуск приложения...")

        python_executable = sys.executable
        script_path = sys.argv[0]

        restart_command = f'"{python_executable}" "{script_path}"'
        logger.info(f"Команда перезапуска: {restart_command}")

        os.execv(python_executable, [python_executable] + sys.argv)

    except Exception as e:
        logger.error(f"Ошибка при перезапуске приложения: {e}")
        logger.critical("Перезапуск не удался, приложение может быть в нерабочем состоянии")
        logger.critical("Выполняется принудительный выход из приложения")
        os._exit(1)