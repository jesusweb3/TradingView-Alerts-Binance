# src/strategies/factory.py

from typing import Union
from src.config.manager import config_manager
from src.strategies.classic_strategy import ClassicStrategy
from src.strategies.stop_strategy import StopStrategy
from src.strategies.hedging_strategy import HedgingStrategy
from src.utils.logger import get_logger

logger = get_logger(__name__)


def create_strategy() -> Union[ClassicStrategy, StopStrategy, HedgingStrategy]:
    """
    Создает экземпляр стратегии на основе конфигурации

    Returns:
        ClassicStrategy, StopStrategy или HedgingStrategy в зависимости от TRADING_STRATEGY

    Raises:
        ValueError: Если указана неподдерживаемая торговая стратегия
    """
    trading_strategy = config_manager.get_trading_strategy()

    if trading_strategy == "classic":
        logger.info("Создана классическая стратегия (без стопов)")
        return ClassicStrategy()
    elif trading_strategy == "stop":
        logger.info("Создана стратегия со стопами")
        return StopStrategy()
    elif trading_strategy == "hedging":
        logger.info("Создана стратегия с хеджированием")
        return HedgingStrategy()
    else:
        raise ValueError(f"Неподдерживаемая торговая стратегия: {trading_strategy}")