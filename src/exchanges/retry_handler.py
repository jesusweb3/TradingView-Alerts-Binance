# src/exchanges/retry_handler.py

import time
from functools import wraps
from src.utils.logger import get_logger

logger = get_logger(__name__)


def retry_on_api_error(max_retries: int = 3, delay: int = 10):
    """
    Декоратор для автоматического повтора API операций при ошибках

    Args:
        max_retries: Максимальное количество попыток (по умолчанию 3)
        delay: Задержка между попытками в секундах (по умолчанию 10)
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None

            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e

                    # Логируем попытку
                    if attempt < max_retries - 1:
                        logger.warning(f"{func.__name__} - попытка {attempt + 1} неудачна: {e}")
                        logger.info(f"Повтор через {delay} секунд...")
                        time.sleep(delay)
                    else:
                        logger.error(f"{func.__name__} - все {max_retries} попытки неудачны")

            # Если все попытки неудачны, выбрасываем последнее исключение
            raise last_exception

        return wrapper

    return decorator