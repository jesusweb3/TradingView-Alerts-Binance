# src/utils/retry_handler.py

import logging
from functools import wraps
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log
)
from src.utils.logger import get_logger

logger = get_logger(__name__)


def retry_on_api_error(
        max_attempts: int = 3,
        min_wait: float = 2.0,
        max_wait: float = 10.0
):
    """
    Async декоратор для автоматического повтора API операций при ошибках

    Использует exponential backoff: 2s, 4s, 8s (до max_wait)

    Args:
        max_attempts: Максимальное количество попыток (по умолчанию 3)
        min_wait: Минимальная задержка между попытками в секундах
        max_wait: Максимальная задержка между попытками в секундах
    """

    def decorator(func):
        @wraps(func)
        @retry(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential(multiplier=1, min=min_wait, max=max_wait),
            retry=retry_if_exception_type(Exception),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True
        )
        async def async_wrapper(*args, **kwargs):
            return await func(*args, **kwargs)

        return async_wrapper

    return decorator