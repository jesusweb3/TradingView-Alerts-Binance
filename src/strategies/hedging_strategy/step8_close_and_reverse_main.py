# src/strategies/hedging_strategy/step8_close_and_reverse_main.py

from typing import Dict, Any, Optional
from src.utils.logger import get_logger

logger = get_logger(__name__)


class CloseAndReverseMain:
    """Закрытие старой основной позиции и открытие новой в противоположном направлении"""

    def __init__(self, exchange):
        self.exchange = exchange
        self.symbol = exchange.symbol

    async def execute(
        self,
        action: str,
        current_price: Optional[float]
    ) -> Dict[str, Any]:
        """
        Закрывает текущую основную позицию и открывает новую

        Args:
            action: 'buy' или 'sell' (новый сигнал)
            current_price: Текущая цена из WebSocket

        Returns:
            {
                'success': bool,
                'close_success': bool,
                'old_position_side': str,
                'old_volume': float,
                'open_success': bool,
                'new_position_side': str,
                'new_entry_price': float,
                'new_quantity': float,
                'new_direction': str,
                'error': str | None
            }
        """
        try:
            current_position = await self.exchange.get_current_position(self.symbol)

            if not current_position:
                logger.warning("Позиция не найдена при разворте")
                return {
                    'success': False,
                    'close_success': False,
                    'old_position_side': None,
                    'old_volume': None,
                    'open_success': False,
                    'new_position_side': None,
                    'new_entry_price': None,
                    'new_quantity': None,
                    'new_direction': None,
                    'error': 'Позиция не найдена'
                }

            old_position_side = current_position['side']
            old_volume = current_position['size']

            logger.info(
                f"Закрываем текущую позицию {old_position_side} x{old_volume}"
            )

            close_success = await self.exchange.close_position(
                symbol=self.symbol,
                position_side=old_position_side,
                quantity=old_volume
            )

            if not close_success:
                logger.error(f"Не удалось закрыть текущую позицию {old_position_side}")
                return {
                    'success': False,
                    'close_success': False,
                    'old_position_side': old_position_side,
                    'old_volume': old_volume,
                    'open_success': False,
                    'new_position_side': None,
                    'new_entry_price': None,
                    'new_quantity': None,
                    'new_direction': None,
                    'error': 'Ошибка закрытия старой позиции'
                }

            logger.info(f"Позиция {old_position_side} x{old_volume} успешно закрыта")

            if current_price is None:
                logger.error("Цена из WebSocket недоступна для открытия новой позиции")
                return {
                    'success': False,
                    'close_success': True,
                    'old_position_side': old_position_side,
                    'old_volume': old_volume,
                    'open_success': False,
                    'new_position_side': None,
                    'new_entry_price': None,
                    'new_quantity': None,
                    'new_direction': None,
                    'error': 'Цена недоступна для открытия новой позиции'
                }

            position_size = await self._get_position_size()

            quantity = self.exchange.calculate_quantity(
                self.symbol,
                position_size,
                current_price
            )

            logger.info(
                f"Рассчитано количество для новой позиции: {quantity} "
                f"(цена ${current_price:.2f})"
            )

            if action == 'buy':
                open_success = await self.exchange.open_long_position(self.symbol, quantity)
                new_position_side = 'LONG'
                new_direction = 'Long'
            else:  # action == 'sell'
                open_success = await self.exchange.open_short_position(self.symbol, quantity)
                new_position_side = 'SHORT'
                new_direction = 'Short'

            if not open_success:
                logger.error(f"Не удалось открыть новую {new_direction} позицию")
                return {
                    'success': False,
                    'close_success': True,
                    'old_position_side': old_position_side,
                    'old_volume': old_volume,
                    'open_success': False,
                    'new_position_side': new_position_side,
                    'new_entry_price': None,
                    'new_quantity': quantity,
                    'new_direction': new_direction,
                    'error': 'Ошибка открытия новой позиции'
                }

            entry_price = await self.exchange.get_exact_entry_price(
                self.symbol,
                new_position_side
            )

            if entry_price is None:
                logger.error(f"Не удалось получить ТВХ для новой {new_direction} позиции")
                return {
                    'success': False,
                    'close_success': True,
                    'old_position_side': old_position_side,
                    'old_volume': old_volume,
                    'open_success': True,
                    'new_position_side': new_position_side,
                    'new_entry_price': None,
                    'new_quantity': quantity,
                    'new_direction': new_direction,
                    'error': 'Не удалось получить ТВХ'
                }

            logger.info(
                f"Разворот завершён: {old_position_side} x{old_volume} → "
                f"{new_direction} x{quantity}, ТВХ=${entry_price:.2f}"
            )

            return {
                'success': True,
                'close_success': True,
                'old_position_side': old_position_side,
                'old_volume': old_volume,
                'open_success': True,
                'new_position_side': new_position_side,
                'new_entry_price': entry_price,
                'new_quantity': quantity,
                'new_direction': new_direction,
                'error': None
            }

        except Exception as e:
            logger.error(f"Ошибка при разворте позиции: {e}")
            return {
                'success': False,
                'close_success': False,
                'old_position_side': None,
                'old_volume': None,
                'open_success': False,
                'new_position_side': None,
                'new_entry_price': None,
                'new_quantity': None,
                'new_direction': None,
                'error': str(e)
            }

    @staticmethod
    async def _get_position_size() -> float:
        """
        Получает размер позиции из конфигурации

        Returns:
            Размер позиции в USDT

        Raises:
            RuntimeError: Если не удалось получить размер позиции
        """
        try:
            from src.config.manager import config_manager

            binance_config = config_manager.get_binance_config()
            position_size = binance_config.get('position_size')

            if position_size is None:
                raise ValueError("В конфигурации не найдено поле position_size")

            return float(position_size)

        except Exception as e:
            logger.error(f"Критическая ошибка получения размера позиции: {e}")
            raise RuntimeError(f"Не удалось загрузить размер позиции из конфигурации: {e}")