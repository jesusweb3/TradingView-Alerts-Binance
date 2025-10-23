# src/strategies/options/factory.py

from typing import Union
from src.config.manager import config_manager
from src.strategies.options.classic_mode import ClassicStrategy
from src.strategies.options.stop_mode import StopStrategy
from src.utils.logger import get_logger

logger = get_logger(__name__)


def create_strategy() -> Union[ClassicStrategy, StopStrategy]:
    """
    Создает экземпляр стратегии на основе конфигурации

    Returns:
        ClassicStrategy или StopStrategy в зависимости от TRADING_OPTION

    Raises:
        ValueError: Если указана неподдерживаемая торговая опция
    """
    trading_option = config_manager.get_trading_option()

    if trading_option == "classic":
        logger.info("Создана классическая стратегия (без стопов)")
        return ClassicStrategy()
    elif trading_option == "stop":
        logger.info("Создана стратегия со стопами")
        return StopStrategy()
    else:
        raise ValueError(f"Неподдерживаемая торговая опция: {trading_option}")