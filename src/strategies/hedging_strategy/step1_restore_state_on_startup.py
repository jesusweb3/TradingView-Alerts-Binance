# src/strategies/hedging_strategy/step1_restore_state_on_startup.py

from typing import Dict, Any
from src.utils.logger import get_logger

logger = get_logger(__name__)


class RestoreStateOnStartup:
    """Восстановление состояния после перезапуска сервера"""

    def __init__(self, exchange):
        self.exchange = exchange
        self.symbol = exchange.symbol

    async def execute(self) -> Dict[str, Any]:
        """
        Получает текущие позиции с биржи и восстанавливает состояние

        Returns:
            {
                'positions_found': bool,
                'main_position_side': Optional[str],  # 'LONG' или 'SHORT'
                'main_entry_price': Optional[float],
                'previous_position_volume': Optional[float],
                'last_action': Optional[str],  # 'buy' или 'sell'
                'hedge_position_side': None,
                'hedge_entry_price': None,
            }
        """
        try:
            all_positions = await self.exchange.get_all_positions(self.symbol)

            if not all_positions:
                logger.info("При старте позиций не обнаружено")
                return self._build_no_positions_response()

            logger.info(f"Обнаружено {len(all_positions)} открытых позиций при старте")
            for pos in all_positions:
                logger.info(
                    f"  {pos['side']}: {pos['size']} по ${pos['entry_price']:.2f}, "
                    f"PnL ${pos['unrealized_pnl']:.2f}"
                )

            main_position = all_positions[0]
            return self._build_positions_exist_response(main_position)

        except Exception as e:
            logger.error(f"Ошибка восстановления состояния: {e}")
            raise

    @staticmethod
    def _build_no_positions_response() -> Dict[str, Any]:
        """Формирует ответ когда позиций нет"""
        return {
            'positions_found': False,
            'main_position_side': None,
            'main_entry_price': None,
            'previous_position_volume': None,
            'last_action': None,
            'hedge_position_side': None,
            'hedge_entry_price': None,
        }

    @staticmethod
    def _build_positions_exist_response(main_position: Dict[str, Any]) -> Dict[str, Any]:
        """Формирует ответ когда позиции есть"""
        position_side = main_position['side']
        last_action = 'buy' if position_side == 'LONG' else 'sell'

        return {
            'positions_found': True,
            'main_position_side': position_side,
            'main_entry_price': main_position['entry_price'],
            'previous_position_volume': main_position['size'],
            'last_action': last_action,
            'hedge_position_side': None,
            'hedge_entry_price': None,
        }