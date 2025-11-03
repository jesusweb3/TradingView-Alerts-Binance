# src/strategies/hedging_strategy/step11_on_activation_reached.py

from typing import Dict, Any
from src.utils.logger import get_logger

logger = get_logger(__name__)


class OnActivationReached:
    """Callback при срабатывании цены активации - открытие хеджа позиции"""

    def __init__(self, exchange, price_stream):
        self.exchange = exchange
        self.price_stream = price_stream
        self.symbol = exchange.symbol

    async def execute(
        self,
        current_price: float,
        main_position_side: str,
        activation_price: float
    ) -> Dict[str, Any]:
        """
        Открывает хедж позицию когда цена активации достигнута

        Args:
            current_price: Текущая цена когда сработала цена активации
            main_position_side: 'LONG' или 'SHORT' (сторона основной позиции)
            activation_price: Цена активации которую только что достигли

        Returns:
            {
                'success': bool,
                'hedge_position_side': str,  # 'LONG' или 'SHORT'
                'hedge_entry_price': float,
                'quantity': float,
                'activation_price_reached': float,
                'current_price': float,
                'error': str | None
            }
        """
        try:
            logger.info(
                f"Цена активации достигнута! "
                f"Цена=${current_price:.2f}, порог был ${activation_price:.2f}"
            )

            hedge_position_side = 'SHORT' if main_position_side == 'LONG' else 'LONG'

            logger.info(
                f"Открываем хедж позицию {hedge_position_side} "
                f"(основная была {main_position_side})"
            )

            position_size = await self._get_position_size()

            quantity = self.exchange.calculate_quantity(
                self.symbol,
                position_size,
                current_price
            )

            logger.info(
                f"Рассчитано количество для хеджа: {quantity} "
                f"(цена ${current_price:.2f})"
            )

            if hedge_position_side == 'LONG':
                open_success = await self.exchange.open_long_position(self.symbol, quantity)
                hedge_direction = 'LONG'
            else:  # SHORT
                open_success = await self.exchange.open_short_position(self.symbol, quantity)
                hedge_direction = 'SHORT'

            if not open_success:
                logger.error(f"Не удалось открыть хедж позицию {hedge_direction}")
                return {
                    'success': False,
                    'hedge_position_side': hedge_position_side,
                    'hedge_entry_price': None,
                    'quantity': quantity,
                    'activation_price_reached': activation_price,
                    'current_price': current_price,
                    'error': 'Ошибка открытия позиции на бирже'
                }

            logger.info(
                f"Хедж позиция {hedge_direction} открыта, получаем точный ТВХ"
            )

            entry_price = await self.exchange.get_exact_entry_price(
                self.symbol,
                hedge_position_side
            )

            if entry_price is None:
                logger.error(f"Не удалось получить точный ТВХ для хеджа {hedge_direction}")
                return {
                    'success': False,
                    'hedge_position_side': hedge_position_side,
                    'hedge_entry_price': None,
                    'quantity': quantity,
                    'activation_price_reached': activation_price,
                    'current_price': current_price,
                    'error': 'Не удалось получить ТВХ с биржи'
                }

            logger.info(
                f"Хедж {hedge_direction} открыта успешно: "
                f"ТВХ=${entry_price:.2f}, объём={quantity}"
            )

            logger.info(
                f"Отменяем watch на цену активации ${activation_price:.2f} "
                f"(больше не нужна)"
            )

            self.price_stream.cancel_all_watches()

            logger.info("Переходим к расчёту уровней SL и TRIGGER")

            return {
                'success': True,
                'hedge_position_side': hedge_position_side,
                'hedge_entry_price': entry_price,
                'quantity': quantity,
                'activation_price_reached': activation_price,
                'current_price': current_price,
                'error': None
            }

        except Exception as e:
            logger.error(f"Ошибка при открытии хеджа: {e}")
            return {
                'success': False,
                'hedge_position_side': None,
                'hedge_entry_price': None,
                'quantity': None,
                'activation_price_reached': activation_price,
                'current_price': current_price,
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