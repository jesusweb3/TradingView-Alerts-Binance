# src/strategies/hedging_strategy/step2_ensure_hedge_mode.py

from typing import Dict, Any
from src.utils.logger import get_logger

logger = get_logger(__name__)


class EnsureHedgeMode:
    """Подготовка аккаунта к режиму хеджирования"""

    def __init__(self, exchange):
        self.exchange = exchange
        self.symbol = exchange.symbol

    async def execute(self, positions_found: bool) -> Dict[str, Any]:
        """
        Переключает аккаунт в режим hedging если позиций нет

        Args:
            positions_found: Флаг из step1 (есть ли открытые позиции)

        Returns:
            {
                'hedge_mode_enabled': bool,
                'skipped': bool,  # True если пропустили потому что есть позиции
                'success': bool
            }
        """
        try:
            if positions_found:
                logger.info("Позиции найдены при старте - режим hedging переключим после закрытия")
                return {
                    'hedge_mode_enabled': False,
                    'skipped': True,
                    'success': True
                }

            logger.info("Позиций нет - переключаем аккаунт в режим hedging")

            success = await self.exchange.enable_hedge_mode()

            if success:
                logger.info("Аккаунт успешно переключен в режим hedging")
                return {
                    'hedge_mode_enabled': True,
                    'skipped': False,
                    'success': True
                }
            else:
                logger.error("Не удалось переключить аккаунт в режим hedging")
                return {
                    'hedge_mode_enabled': False,
                    'skipped': False,
                    'success': False
                }

        except Exception as e:
            logger.error(f"Ошибка переключения режима hedging: {e}")
            raise