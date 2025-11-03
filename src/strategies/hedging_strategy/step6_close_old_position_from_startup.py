# src/strategies/hedging_strategy/step6_close_old_position_from_startup.py

from typing import Dict, Any
from src.utils.logger import get_logger

logger = get_logger(__name__)


class CloseOldPositionFromStartup:
    """Закрытие старой позиции которая была при старте сервера"""

    def __init__(self, exchange):
        self.exchange = exchange
        self.symbol = exchange.symbol

    async def execute(
        self,
        previous_position_side: str,
        previous_position_volume: float
    ) -> Dict[str, Any]:
        """
        Закрывает старую позицию маркет-ордером

        Args:
            previous_position_side: 'LONG' или 'SHORT'
            previous_position_volume: Объём позиции для закрытия

        Returns:
            {
                'success': bool,
                'position_side': str,
                'volume': float,
                'close_side': str,
                'error': str | None
            }
        """
        try:
            close_side = 'SELL' if previous_position_side == 'LONG' else 'BUY'

            logger.info(
                f"Закрываем старую позицию {previous_position_side} x{previous_position_volume} "
                f"маркет-ордером {close_side}"
            )

            success = await self.exchange.close_position(
                symbol=self.symbol,
                position_side=previous_position_side,
                quantity=previous_position_volume
            )

            if success:
                logger.info(f"Старая позиция {previous_position_side} успешно закрыта")
                return {
                    'success': True,
                    'position_side': previous_position_side,
                    'volume': previous_position_volume,
                    'close_side': close_side,
                    'error': None
                }
            else:
                logger.error(f"Не удалось закрыть старую позицию {previous_position_side}")
                return {
                    'success': False,
                    'position_side': previous_position_side,
                    'volume': previous_position_volume,
                    'close_side': close_side,
                    'error': 'Ошибка закрытия позиции на бирже'
                }

        except Exception as e:
            logger.error(f"Ошибка при закрытии старой позиции: {e}")
            return {
                'success': False,
                'position_side': previous_position_side,
                'volume': previous_position_volume,
                'close_side': None,
                'error': str(e)
            }