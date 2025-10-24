# src/config/manager.py

import os
import json
from typing import Optional, Any, List
from functools import lru_cache
from dotenv import load_dotenv
from src.utils.logger import get_logger

logger = get_logger(__name__)


class ConfigManager:
    """Централизованный менеджер конфигурации с .env файлом"""

    _instance: Optional['ConfigManager'] = None

    def __new__(cls) -> 'ConfigManager':
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not hasattr(self, '_initialized'):
            self._load_config()
            self._initialized = True

    @staticmethod
    def _load_config():
        """Загружает конфигурацию из .env файла"""
        env_path = ".env"

        if not os.path.exists(env_path):
            raise FileNotFoundError(f"Файл конфигурации {env_path} не найден")

        try:
            load_dotenv(env_path)
            logger.info("Конфигурация загружена из .env")
        except Exception as e:
            raise ValueError(f"Ошибка загрузки .env файла: {e}")

    @staticmethod
    def _validate_and_get(
        key: str,
        value_type: type,
        required: bool = True,
        default: Any = None,
        min_value: Optional[float] = None,
        max_value: Optional[float] = None
    ) -> Any:
        """
        Универсальный метод получения и валидации значений из .env

        Args:
            key: Ключ переменной окружения
            value_type: Тип для преобразования (str, int, float, bool)
            required: Обязательное ли поле
            default: Значение по умолчанию если не required
            min_value: Минимальное значение (для числовых типов)
            max_value: Максимальное значение (для числовых типов)

        Returns:
            Значение нужного типа

        Raises:
            ValueError: Если поле обязательное и отсутствует, или валидация не прошла
        """
        raw_value = os.getenv(key)

        if required and (raw_value is None or raw_value == ''):
            raise ValueError(f"В .env отсутствует обязательное поле {key}")

        if not required and raw_value is None:
            return default

        try:
            if value_type == bool:
                converted_value = raw_value.lower() in ('true', '1', 'yes', 'on')
            elif value_type == str:
                converted_value = raw_value
                if required and not converted_value:
                    raise ValueError(f"Поле {key} не должно быть пустой строкой")
            else:
                converted_value = value_type(raw_value)
        except (ValueError, TypeError) as e:
            raise ValueError(f"Ошибка преобразования {key} к типу {value_type.__name__}: {e}")

        if value_type in (int, float) and converted_value is not None:
            if min_value is not None and converted_value <= min_value:
                raise ValueError(f"{key} должен быть больше {min_value}")
            if max_value is not None and converted_value >= max_value:
                raise ValueError(f"{key} должен быть меньше {max_value}")

        return converted_value

    @lru_cache(maxsize=1)
    def get_binance_config(self) -> dict:
        """Возвращает полную конфигурацию для Binance с валидацией"""
        api_key = ConfigManager._validate_and_get('BINANCE_API_KEY', str, required=True)
        secret = ConfigManager._validate_and_get('BINANCE_SECRET', str, required=True)
        position_size = ConfigManager._validate_and_get('EXCHANGE_POSITION_SIZE', float, required=True)
        leverage = ConfigManager._validate_and_get('EXCHANGE_LEVERAGE', int, required=True)

        return {
            'api_key': api_key,
            'secret': secret,
            'position_size': position_size,
            'leverage': leverage
        }

    @lru_cache(maxsize=1)
    def get_trading_config(self) -> dict:
        """Возвращает конфигурацию торговли с валидацией"""
        symbol = ConfigManager._validate_and_get('TRADING_SYMBOL', str, required=True)

        return {
            'symbol': symbol
        }

    @lru_cache(maxsize=1)
    def get_server_config(self) -> dict:
        """Возвращает конфигурацию сервера с валидацией"""
        allowed_ips_raw = os.getenv('ALLOWED_IPS', '[]')

        try:
            allowed_ips = json.loads(allowed_ips_raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"Ошибка парсинга ALLOWED_IPS как JSON: {e}")

        if not allowed_ips or not isinstance(allowed_ips, list):
            raise ValueError("В .env поле ALLOWED_IPS должно быть непустым JSON массивом")

        return {
            'allowed_ips': allowed_ips
        }

    @lru_cache(maxsize=1)
    def get_trading_strategy(self) -> str:
        """
        Возвращает выбранную торговую стратегию с валидацией

        Returns:
            'classic' или 'stop'

        Raises:
            ValueError: Если указана неподдерживаемая стратегия
        """
        strategy = ConfigManager._validate_and_get(
            'TRADING_STRATEGY',
            str,
            required=False,
            default='classic'
        ).lower()

        valid_strategies = ['classic', 'stop']

        if strategy not in valid_strategies:
            raise ValueError(
                f"TRADING_STRATEGY должна быть одной из {valid_strategies}, получено: {strategy}"
            )

        return strategy

    @lru_cache(maxsize=1)
    def get_trailing_stop_config(self) -> dict:
        """Возвращает конфигурацию трейлинг стопа с валидацией"""
        activation_percent = ConfigManager._validate_and_get(
            'TRAILING_ACTIVATION_PERCENT',
            float,
            required=True,
            min_value=0.0,
            max_value=100.0
        )

        stop_percent = ConfigManager._validate_and_get(
            'TRAILING_STOP_PERCENT',
            float,
            required=True,
            min_value=0.0,
            max_value=100.0
        )

        return {
            'activation_percent': activation_percent,
            'stop_percent': stop_percent
        }

    @lru_cache(maxsize=1)
    def get_telegram_config(self) -> dict:
        """Возвращает конфигурацию Telegram с валидацией"""
        bot_token = ConfigManager._validate_and_get('TELEGRAM_BOT_TOKEN', str, required=True)
        chat_ids_raw = ConfigManager._validate_and_get('TELEGRAM_CHAT_ID', str, required=True)

        chat_ids: List[str] = [cid.strip() for cid in chat_ids_raw.split(',') if cid.strip()]

        if not chat_ids:
            raise ValueError("TELEGRAM_CHAT_ID не содержит валидных ID")

        return {
            'bot_token': bot_token,
            'chat_ids': chat_ids
        }

    def get_trading_symbol(self) -> str:
        """Возвращает торгуемый символ"""
        trading_config = self.get_trading_config()
        return trading_config['symbol']

    def clear_cache(self):
        """Очищает кеш всех lru_cache методов"""
        self.get_binance_config.cache_clear()
        self.get_trading_config.cache_clear()
        self.get_server_config.cache_clear()
        self.get_trading_strategy.cache_clear()
        self.get_trailing_stop_config.cache_clear()
        self.get_telegram_config.cache_clear()


config_manager = ConfigManager()