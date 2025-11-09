# src/strategies/hedging_strategy/step7_open_new_main_position.py

from typing import Dict, Any, Optional
from src.utils.logger import get_logger

logger = get_logger(__name__)


class OpenNewMainPosition:
    """Открытие новой основной позиции по сигналу"""

    def __init__(self, exchange, price_stream):
        self.exchange = exchange
        self.price_stream = price_stream
        self.symbol = exchange.symbol

    async def execute(
        self,
        action: str,
        current_price: Optional[float]
    ) -> Dict[str, Any]:
        """
        Открывает новую основную позицию (BUY или SELL)

        Args:
            action: 'buy' или 'sell'
            current_price: Текущая цена из WebSocket (может быть None)

        Returns:
            {
                'success': bool,
                'main_position_side': 'LONG' | 'SHORT',
                'main_entry_price': float,
                'quantity': float,
                'direction': str,  # 'Long' или 'Short'
                'error': str | None
            }
        """
        try:
            logger.info("Отменяем все старые watch'и для надёжности")
            try:
                self.price_stream.cancel_all_watches()
                logger.info("Все старые watch'и отменены")
            except Exception as e:
                logger.error(f"Ошибка при отмене старых watch'ей: {e}")

            if current_price is None:
                logger.error("Цена из WebSocket недоступна")
                return {
                    'success': False,
                    'main_position_side': None,
                    'main_entry_price': None,
                    'quantity': None,
                    'direction': None,
                    'error': 'Цена недоступна'
                }

            position_size = await self._get_position_size()

            quantity = self.exchange.calculate_quantity(
                self.symbol,
                position_size,
                current_price
            )

            logger.info(
                f"Рассчитано количество для {self.symbol}: {quantity} "
                f"(размер позиции {position_size} USDT, цена ${current_price:.2f})"
            )

            if action == 'buy':
                success = await self.exchange.open_long_position(self.symbol, quantity)
                position_side = 'LONG'
                direction = 'Long'
            else:  # action == 'sell'
                success = await self.exchange.open_short_position(self.symbol, quantity)
                position_side = 'SHORT'
                direction = 'Short'

            if not success:
                logger.error(f"Не удалось открыть {direction} позицию {self.symbol}")
                return {
                    'success': False,
                    'main_position_side': None,
                    'main_entry_price': None,
                    'quantity': quantity,
                    'direction': direction,
                    'error': 'Ошибка открытия позиции на бирже'
                }

            entry_price = await self.exchange.get_exact_entry_price(self.symbol, position_side)

            if entry_price is None:
                logger.error(f"Не удалось получить точный ТВХ для {position_side} позиции")
                return {
                    'success': False,
                    'main_position_side': position_side,
                    'main_entry_price': None,
                    'quantity': quantity,
                    'direction': direction,
                    'error': 'Не удалось получить ТВХ с биржи'
                }

            logger.info(
                f"Позиция {direction} открыта для {self.symbol}: "
                f"ТВХ=${entry_price:.2f}, объём={quantity}"
            )

            return {
                'success': True,
                'main_position_side': position_side,
                'main_entry_price': entry_price,
                'quantity': quantity,
                'direction': direction,
                'error': None
            }

        except Exception as e:
            logger.error(f"Ошибка открытия новой основной позиции: {e}")
            return {
                'success': False,
                'main_position_side': None,
                'main_entry_price': None,
                'quantity': None,
                'direction': None,
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